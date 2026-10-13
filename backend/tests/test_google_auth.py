from urllib.parse import parse_qs, urlparse

from app import db, google_oauth


def _state_from(location: str) -> str:
    return parse_qs(urlparse(location).query)["state"][0]


def test_start_redirects_to_google(client):
    res = client.get("/api/v1/auth/google/start", follow_redirects=False)
    assert res.status_code in (302, 307)
    location = res.headers["location"]
    assert location.startswith("https://accounts.google.com/")
    assert "client_id=test-client" in location
    assert "state=" in location


def test_callback_creates_user_and_session(client, monkeypatch):
    monkeypatch.setattr(
        google_oauth,
        "exchange_code",
        lambda code, uri: {"access_token": "at", "expires_in": 3600},
    )
    monkeypatch.setattr(
        google_oauth,
        "fetch_userinfo",
        lambda token: {
            "sub": "sub-1",
            "email": "Kid@Example.com",
            "email_verified": True,
            "name": "Kid Example",
            "picture": "https://example.com/p.png",
        },
    )
    start = client.get("/api/v1/auth/google/start", follow_redirects=False)
    state = _state_from(start.headers["location"])
    cb = client.get(
        f"/api/v1/auth/google/callback?code=abc&state={state}",
        follow_redirects=False,
    )
    assert cb.status_code in (302, 307)

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "kid@example.com"  # normalised
    assert body["displayName"] == "Kid Example"
    assert body["gmailConnected"] is False
    assert db.get_user_by_google_sub("sub-1") is not None


def test_callback_links_existing_email_account(client, monkeypatch):
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO users (id, email, display_name, password_hash, created_at)"
        " VALUES ('u-legacy', 'kid@example.com', 'Old Name', 'salt$hash', '2020-01-01T00:00:00+00:00')"
    )
    conn.commit()

    monkeypatch.setattr(
        google_oauth, "exchange_code", lambda code, uri: {"access_token": "at"}
    )
    monkeypatch.setattr(
        google_oauth,
        "fetch_userinfo",
        lambda token: {
            "sub": "sub-legacy",
            "email": "kid@example.com",
            "email_verified": True,
            "name": "New Name",
        },
    )
    start = client.get("/api/v1/auth/google/start", follow_redirects=False)
    state = _state_from(start.headers["location"])
    client.get(
        f"/api/v1/auth/google/callback?code=abc&state={state}",
        follow_redirects=False,
    )

    linked = db.get_user_by_google_sub("sub-legacy")
    assert linked is not None
    assert linked["id"] == "u-legacy"  # same account, chats preserved
    assert client.get("/api/v1/auth/me").json()["id"] == "u-legacy"


def test_callback_rejects_unverified_email(client, monkeypatch):
    monkeypatch.setattr(
        google_oauth, "exchange_code", lambda code, uri: {"access_token": "at"}
    )
    monkeypatch.setattr(
        google_oauth,
        "fetch_userinfo",
        lambda token: {
            "sub": "sub-x",
            "email": "nope@example.com",
            "email_verified": False,
        },
    )
    start = client.get("/api/v1/auth/google/start", follow_redirects=False)
    state = _state_from(start.headers["location"])
    cb = client.get(
        f"/api/v1/auth/google/callback?code=abc&state={state}",
        follow_redirects=False,
    )
    assert "auth_error=email_unverified" in cb.headers["location"]
    assert client.get("/api/v1/auth/me").status_code == 401
    assert db.get_user_by_google_sub("sub-x") is None


def test_callback_rejects_unknown_state(client, monkeypatch):
    cb = client.get(
        "/api/v1/auth/google/callback?code=abc&state=not-a-real-state",
        follow_redirects=False,
    )
    assert "auth_error=invalid_state" in cb.headers["location"]


def test_state_is_single_use(client):
    state = db.create_oauth_state("login")
    assert db.consume_oauth_state(state, "login") is not None
    assert db.consume_oauth_state(state, "login") is None


def test_schema_allows_oauth_only_users(isolated_db):
    conn = db.get_conn()
    cols = {r["name"]: r["notnull"] for r in conn.execute("PRAGMA table_info(users)")}
    assert cols["password_hash"] == 0
    assert "google_sub" in cols
    assert "avatar_url" in cols
    plan = db.create_google_user("a@b.com", "A", "sub-a")
    assert plan["email"] == "a@b.com"


def test_adult_mode_defaults_off_and_round_trips(signed_in):
    assert signed_in.get("/api/v1/auth/me").json()["adultMode"] is False
    assert signed_in.get("/api/v1/auth/me").json()["adultModeEnabled"] is True

    res = signed_in.patch("/api/v1/auth/me", json={"adultMode": True})
    assert res.status_code == 200
    assert res.json()["adultMode"] is True
    assert signed_in.get("/api/v1/auth/me").json()["adultMode"] is True

    # a name-only patch leaves the flag alone
    signed_in.patch("/api/v1/auth/me", json={"displayName": "New Name"})
    me = signed_in.get("/api/v1/auth/me").json()
    assert me["displayName"] == "New Name"
    assert me["adultMode"] is True


def test_state_carries_adult_mode(signed_in):
    from app import db, main
    from app.schemas import ChatRequest

    signed_in.patch("/api/v1/auth/me", json={"adultMode": True})
    user = db.get_user_by_id(signed_in.get("/api/v1/auth/me").json()["id"])
    assert main._state(ChatRequest(chatId="x", messages=[]), user)["adult"] is True


def test_adult_mode_disabled_hides_option(signed_in, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "ADULT_MODE_ENABLED", False)

    me = signed_in.get("/api/v1/auth/me").json()
    assert me["adultModeEnabled"] is False
    assert me["adultMode"] is False

    # PATCHing the flag is ignored while the option is off
    res = signed_in.patch("/api/v1/auth/me", json={"adultMode": True})
    assert res.status_code == 200
    assert res.json()["adultMode"] is False


def test_disabled_adult_mode_suppresses_stored_flag(signed_in, monkeypatch):
    from app import db, main
    from app.schemas import ChatRequest

    signed_in.patch("/api/v1/auth/me", json={"adultMode": True})
    user = db.get_user_by_id(signed_in.get("/api/v1/auth/me").json()["id"])
    assert main._state(ChatRequest(chatId="x", messages=[]), user)["adult"] is True

    # An operator flips the env off: the stored flag stops counting.
    monkeypatch.setattr(main, "ADULT_MODE_ENABLED", False)
    assert main._state(ChatRequest(chatId="x", messages=[]), user)["adult"] is False
