import re
from urllib.parse import parse_qs, urlparse

import pytest

from app import auth, db, mailer, ratelimit, turnstile

PASSWORD = "correct horse battery"


@pytest.fixture
def mail(client, monkeypatch):
    """Captured outgoing mail: a list of (kind, to, lang, name, link)."""
    sent: list[tuple] = []
    monkeypatch.setattr(
        mailer, "send_kind", lambda kind, to, lang, name, link="": sent.append(
            (kind, to, lang, name, link)
        )
    )
    monkeypatch.setattr(turnstile, "verify", lambda token, ip="": token == "ok")
    ratelimit.reset()
    yield sent
    ratelimit.reset()


def _token(link: str) -> str:
    return parse_qs(urlparse(link).query)["token"][0]


def _register(client, email="kid@example.com", password=PASSWORD, **extra):
    body = {"email": email, "password": password, "name": "Kid", "turnstileToken": "ok"}
    return client.post("/api/v1/auth/register", json={**body, **extra})


def _registered(client, mail, email="kid@example.com"):
    """Register and confirm; returns the confirmed user's JSON."""
    _register(client, email)
    res = client.post("/api/v1/auth/confirm", json={"token": _token(mail[-1][4])})
    assert res.status_code == 200
    return res.json()


def test_register_waits_for_confirmation(client, mail):
    res = _register(client, "Kid@Example.com", lang="it")
    assert res.status_code == 202
    assert res.json() == {"status": "check_email"}
    assert db.get_user_by_email("kid@example.com") is None
    kind, to, lang, name, link = mail[-1]
    assert (kind, to, lang, name) == ("confirm", "kid@example.com", "it", "Kid")
    assert link.startswith("http://localhost:5173/confirm-email?token=")

    res = client.post("/api/v1/auth/confirm", json={"token": _token(link)})
    assert res.status_code == 200
    assert res.json()["email"] == "kid@example.com"
    assert res.json()["hasPassword"] is True
    assert client.get("/api/v1/auth/me").status_code == 200
    user = db.get_user_by_email("kid@example.com")
    assert user["password_hash"].startswith("$argon2id$")

    again = client.post("/api/v1/auth/confirm", json={"token": _token(link)})
    assert again.status_code == 400
    assert again.json()["detail"]["code"] == "link_invalid"


def test_register_needs_turnstile(client, mail):
    res = _register(client, turnstileToken="bad")
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "turnstile_failed"
    assert mail == []


def test_register_rejects_short_password_and_bad_email(client, mail):
    assert _register(client, password="short").json()["detail"]["code"] == "password_too_short"
    assert _register(client, email="nope").json()["detail"]["code"] == "invalid_email"


def test_login_before_confirming(client, mail):
    _register(client)
    res = client.post("/api/v1/auth/login", json={"email": "kid@example.com", "password": PASSWORD})
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "email_unconfirmed"
    # A wrong password does not reveal that a sign-up is waiting.
    res = client.post("/api/v1/auth/login", json={"email": "kid@example.com", "password": "x" * 9})
    assert res.json()["detail"]["code"] == "bad_credentials"


def test_login_after_confirming(client, mail):
    _registered(client, mail)
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/auth/me").status_code == 401

    bad = client.post("/api/v1/auth/login", json={"email": "kid@example.com", "password": "wrong-pass"})
    assert bad.status_code == 401
    assert bad.json()["detail"]["code"] == "bad_credentials"
    unknown = client.post("/api/v1/auth/login", json={"email": "who@example.com", "password": PASSWORD})
    assert unknown.json()["detail"]["code"] == "bad_credentials"

    ok = client.post("/api/v1/auth/login", json={"email": " KID@example.com ", "password": PASSWORD})
    assert ok.status_code == 200
    assert client.get("/api/v1/auth/me").json()["email"] == "kid@example.com"


def test_login_rehashes_weaker_hash(client, mail, monkeypatch):
    _registered(client, mail)
    weak = auth.PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    user = db.get_user_by_email("kid@example.com")
    db.set_password_hash(user["id"], weak.hash(PASSWORD))
    client.post("/api/v1/auth/login", json={"email": "kid@example.com", "password": PASSWORD})
    assert not auth.needs_rehash(db.get_user_by_id(user["id"])["password_hash"])


def test_register_existing_google_account(signed_in, mail):
    client = signed_in
    client.post("/api/v1/auth/logout")
    res = _register(client)
    assert res.status_code == 202
    assert res.json() == {"status": "check_email"}
    assert mail[-1][0] == "exists_google"
    assert db.get_pending_registration("kid@example.com") is None
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 1


def test_register_existing_password_account(client, mail):
    _registered(client, mail)
    _register(client)
    assert mail[-1][0] == "exists_password"


def test_resend_confirmation_replaces_link(client, mail):
    _register(client)
    first = _token(mail[-1][4])
    res = client.post("/api/v1/auth/resend-confirmation", json={"email": "kid@example.com"})
    assert res.status_code == 202
    second = _token(mail[-1][4])
    assert first != second
    assert client.post("/api/v1/auth/confirm", json={"token": first}).status_code == 400
    assert client.post("/api/v1/auth/confirm", json={"token": second}).status_code == 200


def test_resend_for_unknown_address_sends_nothing(client, mail):
    res = client.post("/api/v1/auth/resend-confirmation", json={"email": "who@example.com"})
    assert res.status_code == 202
    assert mail == []


def test_auth_rate_limit(client, mail, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "AUTH_RATE_LIMIT_PER_MINUTE", 3)
    body = {"email": "kid@example.com", "password": "wrong-pass"}
    codes = [client.post("/api/v1/auth/login", json=body).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


def test_forgot_and_reset_password(client, mail):
    _registered(client, mail)
    other = client.__class__(client.app)
    other.post("/api/v1/auth/login", json={"email": "kid@example.com", "password": PASSWORD})
    assert other.get("/api/v1/auth/me").status_code == 200

    res = client.post("/api/v1/auth/forgot-password", json={"email": "kid@example.com"})
    assert res.status_code == 202
    kind, _, _, _, link = mail[-1]
    assert kind == "reset"
    assert link.startswith("http://localhost:5173/reset-password?token=")

    short = client.post("/api/v1/auth/reset-password", json={"token": _token(link), "newPassword": "short"})
    assert short.json()["detail"]["code"] == "password_too_short"

    res = client.post(
        "/api/v1/auth/reset-password", json={"token": _token(link), "newPassword": "brand new pass"}
    )
    assert res.status_code == 200
    # Every other session ends; this browser gets a fresh one.
    assert other.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 200
    again = client.post(
        "/api/v1/auth/reset-password", json={"token": _token(link), "newPassword": "brand new pass"}
    )
    assert again.json()["detail"]["code"] == "link_invalid"
    login = client.post(
        "/api/v1/auth/login", json={"email": "kid@example.com", "password": "brand new pass"}
    )
    assert login.status_code == 200


def test_expired_reset_link(client, mail):
    _registered(client, mail)
    client.post("/api/v1/auth/forgot-password", json={"email": "kid@example.com"})
    token = _token(mail[-1][4])
    db._execute("UPDATE password_resets SET expires_at = now() - interval '1 minute'")
    res = client.post("/api/v1/auth/reset-password", json={"token": token, "newPassword": "brand new pass"})
    assert res.json()["detail"]["code"] == "link_invalid"


def test_purge_drops_expired_tokens(client, mail):
    _register(client, "a@example.com")
    _register(client, "b@example.com")
    db._execute(
        "UPDATE pending_registrations SET expires_at = now() - interval '1 minute'"
        " WHERE email = 'a@example.com'"
    )
    db.purge_expired_email_tokens()
    with db.connection() as conn:
        rows = conn.execute("SELECT email FROM pending_registrations").fetchall()
    assert [r["email"] for r in rows] == ["b@example.com"]


def test_forgot_password_unknown_address(client, mail):
    res = client.post("/api/v1/auth/forgot-password", json={"email": "who@example.com"})
    assert res.status_code == 202
    assert mail == []


def test_change_password_requires_current(client, mail):
    _registered(client, mail)
    wrong = client.post(
        "/api/v1/auth/change-password",
        json={"currentPassword": "nope-nope", "newPassword": "another pass"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "wrong_password"
    ok = client.post(
        "/api/v1/auth/change-password",
        json={"currentPassword": PASSWORD, "newPassword": "another pass"},
    )
    assert ok.status_code == 200
    assert mail[-1][0] == "password_changed"
    # The session that made the change stays signed in.
    assert client.get("/api/v1/auth/me").status_code == 200


def test_google_account_can_set_first_password(signed_in, mail):
    client = signed_in
    assert client.get("/api/v1/auth/me").json()["hasPassword"] is False
    res = client.post("/api/v1/auth/change-password", json={"newPassword": "first password"})
    assert res.status_code == 200
    assert res.json()["hasPassword"] is True
    client.post("/api/v1/auth/logout")
    login = client.post(
        "/api/v1/auth/login", json={"email": "kid@example.com", "password": "first password"}
    )
    assert login.status_code == 200


def test_google_sign_in_links_password_account(client, mail, monkeypatch):
    from app import google_oauth

    user = _registered(client, mail)
    client.post("/api/v1/auth/logout")
    monkeypatch.setattr(google_oauth, "exchange_code", lambda code, uri: {"access_token": "at"})
    monkeypatch.setattr(
        google_oauth,
        "fetch_userinfo",
        lambda token: {"sub": "sub-9", "email": "kid@example.com", "email_verified": True},
    )
    start = client.get("/api/v1/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get(f"/api/v1/auth/google/callback?code=abc&state={state}", follow_redirects=False)
    me = client.get("/api/v1/auth/me").json()
    assert me["id"] == user["id"]
    assert me["hasPassword"] is True


def test_mail_failure_is_reported(client, mail, monkeypatch):
    def boom(*args, **kwargs):
        raise mailer.MailError("connection refused")

    monkeypatch.setattr(mailer, "send_kind", boom)
    res = _register(client)
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "mail_failed"


def test_mail_templates_render_every_language():
    for lang in ("en", "it", "id", "ur", "ur-Latn", "xx"):
        for kind in ("confirm", "reset", "exists_google", "exists_password", "password_changed"):
            subject, text, html_body = mailer.render(kind, lang, "<Kid>", "https://a.test/x?token=t")
            assert subject
            assert "{name}" not in text
            assert "&lt;Kid&gt;" in html_body and "<Kid>" not in html_body
            if kind in ("confirm", "reset"):
                assert "https://a.test/x?token=t" in text
                assert re.search(r'href="https://a\.test/x\?token=t"', html_body)
