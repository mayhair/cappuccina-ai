import httpx
import pytest

from app import config, db, tts


def _mock_transport(body, status=200):
    """httpx transport that answers /v1/tts with an MP3 body."""

    def handler(request):
        assert request.url.host == "api.fish.audio"
        assert request.headers["authorization"].startswith("Bearer ")
        assert request.headers["model"] == config.FISH_TTS_MODEL
        sent = request.read()
        assert b'"format":"mp3"' in sent.replace(b" ", b"")

        return httpx.Response(
            status,
            headers={"content-type": "audio/mpeg"},
            content=body,
            request=request,
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def fish_ok(monkeypatch):
    client = httpx.AsyncClient(transport=_mock_transport(b"ID3mp3-frame"))
    monkeypatch.setattr(tts.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(config, "FISH_API_KEY", "test-fish-key")
    yield
    tts._pending.clear()


@pytest.fixture
def fish_down(monkeypatch):
    def handler(request):
        return httpx.Response(
            402,
            json={"status": 402, "message": "Out of credits"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(tts.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(config, "FISH_API_KEY", "test-fish-key")
    yield


def test_tts_requires_sign_in(client, fish_ok):
    assert client.post("/api/v1/tts", json={"text": "hi"}).status_code == 401
    assert client.get("/api/v1/tts/some-id").status_code == 401


def test_tts_full_flow(signed_in, fish_ok):
    started = signed_in.post("/api/v1/tts", json={"text": "Ciao! Shall we begin?"})
    assert started.status_code == 200
    job_id = started.json()["id"]

    audio = signed_in.get(f"/api/v1/tts/{job_id}")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/mpeg")
    assert audio.content == b"ID3mp3-frame"


def test_tts_job_is_single_use(signed_in, fish_ok):
    job_id = signed_in.post("/api/v1/tts", json={"text": "hello"}).json()["id"]
    assert signed_in.get(f"/api/v1/tts/{job_id}").status_code == 200
    # Second fetch fails: the job was consumed.
    assert signed_in.get(f"/api/v1/tts/{job_id}").status_code == 404


def test_tts_job_belongs_to_one_user(signed_in, client, fish_ok):
    import app.main as main

    # The plain `client` fixture signs in as a different Google identity.
    user2 = db.create_google_user("other@example.com", "Other User", "google-sub-2")
    token = main.db.create_session(user2["id"])

    job_id = signed_in.post("/api/v1/tts", json={"text": "hello"}).json()["id"]
    client.cookies.set("cappuccina_session", token)
    assert client.get(f"/api/v1/tts/{job_id}").status_code == 404


def test_tts_unknown_job_is_404(signed_in, fish_ok):
    assert signed_in.get("/api/v1/tts/not-a-job").status_code == 404


def test_tts_upstream_error_becomes_502(signed_in, fish_down):
    job_id = signed_in.post("/api/v1/tts", json={"text": "hello"}).json()["id"]
    response = signed_in.get(f"/api/v1/tts/{job_id}")
    assert response.status_code == 502
    assert "402" in response.json()["detail"]


def test_tts_without_api_key_is_503(signed_in, monkeypatch):
    import app.main as main

    monkeypatch.setattr(config, "FISH_API_KEY", "")
    monkeypatch.setattr(main, "FISH_API_KEY", "")
    response = signed_in.post("/api/v1/tts", json={"text": "hello"})
    assert response.status_code == 503


def test_tts_rejects_bad_text(signed_in, fish_ok):
    assert signed_in.post("/api/v1/tts", json={"text": ""}).status_code == 422
    assert signed_in.post("/api/v1/tts", json={"text": "x" * 10_001}).status_code == 422


def test_pending_registry_prunes(monkeypatch):
    monkeypatch.setattr(tts, "MAX_PENDING", 3)
    for _ in range(3):
        tts.create_job("user-1", "filler")
    job_id = tts.create_job("user-1", "one more")
    assert len(tts._pending) <= 3
    assert job_id in tts._pending


def test_pending_registry_expires(monkeypatch):
    monkeypatch.setattr(tts, "PENDING_TTL", -1.0)
    job_id = tts.create_job("user-1", "stale")
    assert job_id not in tts._pending
