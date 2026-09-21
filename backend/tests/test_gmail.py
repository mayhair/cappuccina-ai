import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from app import db, gmail, google_oauth

GMAIL_SCOPES = " ".join(
    [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.send",
    ]
)


def _connect_gmail(client, monkeypatch):
    monkeypatch.setattr(
        google_oauth,
        "exchange_code",
        lambda code, uri: {
            "access_token": "gmail-at",
            "refresh_token": "gmail-rt",
            "expires_in": 3600,
            "scope": GMAIL_SCOPES,
        },
    )
    start = client.get("/api/v1/gmail/connect", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    return client.get(
        f"/api/v1/gmail/callback?code=xyz&state={state}", follow_redirects=False
    )


def test_status_disconnected(signed_in):
    body = signed_in.get("/api/v1/gmail/status").json()
    assert body["connected"] is False
    assert body["scopes"] == []


def test_connect_status_and_disconnect(signed_in, monkeypatch):
    cb = _connect_gmail(signed_in, monkeypatch)
    assert "gmail=connected" in cb.headers["location"]

    status = signed_in.get("/api/v1/gmail/status").json()
    assert status["connected"] is True
    assert any("gmail.readonly" in s for s in status["scopes"])
    assert signed_in.get("/api/v1/auth/me").json()["gmailConnected"] is True

    assert signed_in.post("/api/v1/gmail/disconnect").json()["ok"] is True
    assert signed_in.get("/api/v1/gmail/status").json()["connected"] is False


def test_connect_requires_matching_state(signed_in):
    res = signed_in.get(
        "/api/v1/gmail/callback?code=xyz&state=bogus", follow_redirects=False
    )
    assert "gmail=error" in res.headers["location"]


def test_send_draft_success(signed_in, monkeypatch):
    _connect_gmail(signed_in, monkeypatch)
    monkeypatch.setattr(
        gmail, "send_draft", lambda uid, did: {"message_id": "m1", "thread_id": "t1"}
    )
    res = signed_in.post("/api/v1/gmail/drafts/d123/send")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "message_id": "m1", "thread_id": "t1"}


def test_send_draft_uses_correct_endpoint(isolated_db, monkeypatch):
    # Regression: sending uses POST /drafts/send with the id in the body.
    # A /drafts/{id}/send path 404s, which was surfacing as a bogus 409.
    calls = []

    def fake_request(user_id, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"id": "sent-1", "threadId": "t-1"}

    monkeypatch.setattr(gmail, "_request", fake_request)
    result = gmail.send_draft("u1", "r-123")
    assert result == {"message_id": "sent-1", "thread_id": "t-1"}
    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", "/drafts/send")
    assert kwargs["json"] == {"id": "r-123"}


def test_send_draft_already_sent(signed_in, monkeypatch):
    _connect_gmail(signed_in, monkeypatch)

    def gone(uid, did):
        raise gmail.DraftGone("not found")

    monkeypatch.setattr(gmail, "send_draft", gone)
    res = signed_in.post("/api/v1/gmail/drafts/d123/send")
    assert res.status_code == 409
    assert "no longer exists" in res.json()["detail"]


def test_send_draft_requires_connection(signed_in):
    res = signed_in.post("/api/v1/gmail/drafts/d123/send")
    assert res.status_code == 409


def test_sent_draft_survives_reload(signed_in, monkeypatch):
    _connect_gmail(signed_in, monkeypatch)
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    draft = {
        "status": "draft_created",
        "draft_id": "d-1",
        "to": "a@b.com",
        "subject": "s",
        "body": "b",
    }
    result = json.dumps(draft)
    db.append_chat_message(
        chat["id"],
        role="assistant",
        content="Draft ready",
        parts=[
            {"type": "tool", "id": "t1", "name": "draft_email", "args": {}, "result": result}
        ],
        tools=[{"id": "t1", "name": "draft_email", "result": result}],
    )

    # Before sending, the card is not flagged as sent.
    first = signed_in.get("/api/v1/chats").json()
    assert first[0]["messages"][0]["parts"][0].get("sent") is not True

    monkeypatch.setattr(
        gmail, "send_draft", lambda uid, did: {"message_id": "m1", "thread_id": "t1"}
    )
    assert signed_in.post("/api/v1/gmail/drafts/d-1/send").status_code == 200

    reloaded = signed_in.get("/api/v1/chats").json()
    message = reloaded[0]["messages"][0]
    assert message["parts"][0]["sent"] is True
    assert message["tools"][0]["sent"] is True
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    assert db.get_sent_draft_ids(user["id"]) == {"d-1"}


def test_update_draft_success(signed_in, monkeypatch):
    _connect_gmail(signed_in, monkeypatch)
    monkeypatch.setattr(
        gmail,
        "update_draft",
        lambda uid, did, to, subject, body: {
            "draft_id": did,
            "to": to,
            "subject": subject,
            "body": body,
        },
    )
    res = signed_in.patch(
        "/api/v1/gmail/drafts/d123",
        json={"to": "new@example.com", "subject": "Edited", "body": "My name is Sam"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["to"] == "new@example.com"
    assert body["body"] == "My name is Sam"


def test_update_draft_requires_recipient(signed_in, monkeypatch):
    _connect_gmail(signed_in, monkeypatch)
    res = signed_in.patch(
        "/api/v1/gmail/drafts/d123", json={"to": "   ", "subject": "s", "body": "b"}
    )
    assert res.status_code == 400


def test_update_draft_already_sent(signed_in, monkeypatch):
    _connect_gmail(signed_in, monkeypatch)

    def gone(uid, did, to, subject, body):
        raise gmail.DraftGone("nope")

    monkeypatch.setattr(gmail, "update_draft", gone)
    res = signed_in.patch(
        "/api/v1/gmail/drafts/d123", json={"to": "a@b.com", "subject": "s", "body": "b"}
    )
    assert res.status_code == 409
    assert "no longer exists" in res.json()["detail"]


def test_update_draft_preserves_thread(isolated_db, monkeypatch):
    calls = []

    def fake_request(user_id, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return {"message": {"threadId": "thread-9"}}
        return {}

    monkeypatch.setattr(gmail, "_request", fake_request)
    result = gmail.update_draft("u1", "d1", "a@b.com", "Hi", "Body")
    assert result["draft_id"] == "d1"
    put = [c for c in calls if c[0] == "PUT"][0]
    assert put[2]["json"]["message"]["threadId"] == "thread-9"


def test_token_encryption_roundtrip(isolated_db, monkeypatch):
    monkeypatch.setattr(google_oauth, "TOKEN_ENC_KEY", "some-hex-key-abc")
    blob = google_oauth.encrypt_token("super-secret")
    assert blob != "super-secret"
    assert google_oauth.decrypt_token(blob) == "super-secret"
    assert google_oauth.decrypt_token("") == ""


def _user_with_grant(refresh: str) -> str:
    uid = db.create_google_user("u1@example.com", "U1", "sub-u1")["id"]
    db.save_google_grant(
        uid,
        "gmail",
        scopes="scope",
        access_token=google_oauth.encrypt_token("old-at"),
        refresh_token=google_oauth.encrypt_token(refresh),
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
    )
    return uid


def test_access_token_refreshes_when_expired(isolated_db, monkeypatch):
    monkeypatch.setattr(google_oauth, "TOKEN_ENC_KEY", "some-hex-key-abc")
    uid = _user_with_grant("refresh-rt")
    calls = {}

    def fake_post(data):
        calls.update(data)
        return {"access_token": "new-at", "expires_in": 3600}

    monkeypatch.setattr(google_oauth, "_post_token", fake_post)
    assert google_oauth.get_access_token(uid, "gmail") == "new-at"
    assert calls["grant_type"] == "refresh_token"
    assert calls["refresh_token"] == "refresh-rt"
    grant = db.get_google_grant(uid, "gmail")
    assert grant is not None
    assert google_oauth.decrypt_token(grant["access_token"]) == "new-at"
    assert google_oauth.decrypt_token(grant["refresh_token"]) == "refresh-rt"


def test_refresh_failure_drops_grant(isolated_db, monkeypatch):
    monkeypatch.setattr(google_oauth, "TOKEN_ENC_KEY", "some-hex-key-abc")
    uid = _user_with_grant("bad-rt")

    def fail(data):
        raise google_oauth.GoogleOAuthError("invalid_grant")

    monkeypatch.setattr(google_oauth, "_post_token", fail)
    try:
        google_oauth.get_access_token(uid, "gmail")
        assert False, "expected ServiceNotConnected"
    except google_oauth.ServiceNotConnected:
        pass
    assert db.get_google_grant(uid, "gmail") is None


def test_tools_report_missing_connection(isolated_db):
    from agent import tools

    config = {"configurable": {"user_id": "nobody"}}
    assert "isn't connected" in tools.search_emails.invoke(
        {"query": "from:teacher"}, config=config
    ).lower()
    assert "isn't connected" in tools.draft_email.invoke(
        {"to": "a@b.com", "subject": "Hi", "body": "Hello"}, config=config
    ).lower()


def test_draft_tool_returns_reviewable_json(isolated_db, monkeypatch):
    from agent import tools

    monkeypatch.setattr(
        gmail,
        "create_draft",
        lambda uid, to, subject, body: {
            "draft_id": "d-1",
            "message_id": "m-1",
            "to": to,
            "subject": subject,
            "body": body,
        },
    )
    import json

    out = tools.draft_email.invoke(
        {"to": "teacher@school.edu", "subject": "Question", "body": "Hello!"},
        config={"configurable": {"user_id": "u1"}},
    )
    data = json.loads(out)
    assert data["status"] == "draft_created"
    assert data["draft_id"] == "d-1"
    assert data["to"] == "teacher@school.edu"


def test_tool_schema_hides_injected_config():
    from agent import tools

    assert "config" not in tools.search_emails.args
    assert set(tools.search_emails.args) == {"query", "max_results"}
    assert set(tools.draft_email.args) == {"to", "subject", "body"}
