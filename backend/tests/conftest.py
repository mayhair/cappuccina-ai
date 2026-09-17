import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import db  # noqa: E402


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point the sqlite layer at a throwaway file for one test."""
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "_conn", None)
    yield
    if db._conn is not None:
        db._conn.close()
    db._conn = None


@pytest.fixture
def client(isolated_db, monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient

    # Pretend Google credentials exist and keep tokens decryptable.
    monkeypatch.setattr(main.google_oauth, "GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setattr(main.google_oauth, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(main.google_oauth, "TOKEN_ENC_KEY", "test-enc-key")
    return TestClient(main.app)


@pytest.fixture
def signed_in(client, monkeypatch):
    """A client with an authenticated session, signed in via fake Google."""
    from app import google_oauth

    monkeypatch.setattr(
        google_oauth,
        "exchange_code",
        lambda code, redirect_uri: {
            "access_token": "login-at",
            "expires_in": 3600,
            "scope": "openid email profile",
        },
    )
    monkeypatch.setattr(
        google_oauth,
        "fetch_userinfo",
        lambda token: {
            "sub": "google-sub-1",
            "email": "kid@example.com",
            "email_verified": True,
            "name": "Kid Example",
            "picture": "https://example.com/p.png",
        },
    )
    from urllib.parse import parse_qs, urlparse

    start = client.get("/api/v1/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get(
        f"/api/v1/auth/google/callback?code=abc&state={state}",
        follow_redirects=False,
    )
    return client
