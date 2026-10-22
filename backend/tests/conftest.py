import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import os  # noqa: E402

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402
from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

from app import db  # noqa: E402

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://cappuccina:cappuccina@127.0.0.1:5432/cappuccina_test",
)

# Nothing in the suite may touch the development database, even a test that
# forgets the isolated_db fixture: the db layer opens its pool lazily from this.
db.DATABASE_URL = TEST_DATABASE_URL


def _ensure_test_database() -> None:
    """Create the test database if missing; stop with a clear hint if PG is down."""
    name = conninfo_to_dict(TEST_DATABASE_URL).get("dbname") or ""
    if "test" not in name:
        pytest.exit(
            f"Refusing to run: the test database {name!r} is wiped between tests, so its "
            "name must contain 'test'. Set TEST_DATABASE_URL accordingly.",
            returncode=2,
        )
    try:
        with psycopg.connect(
            make_conninfo(TEST_DATABASE_URL, dbname="postgres"),
            autocommit=True,
            connect_timeout=5,
        ) as admin:
            exists = admin.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone()
            if not exists:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except psycopg.OperationalError as exc:
        pytest.exit(
            "Cannot reach PostgreSQL for the tests. Start the local database with "
            "`podman compose up -d` (or `docker compose up -d`) from the repo root, "
            f"or point TEST_DATABASE_URL at a running server.\n{exc}",
            returncode=2,
        )


@pytest.fixture(scope="session")
def _test_database():
    """Fresh schema once per session; each test then just empties the tables."""
    _ensure_test_database()
    db.close_pool()
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    db.get_pool()  # applies the migrations
    yield
    db.close_pool()


@pytest.fixture
def isolated_db(_test_database, tmp_path, monkeypatch):
    """An empty database (and a throwaway data directory) for one test."""
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    with db.connection() as conn:
        tables = [
            r["tablename"]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables"
                " WHERE schemaname = 'public' AND tablename <> 'schema_migrations'"
            )
        ]
        conn.execute(
            sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(", ").join(sql.Identifier(t) for t in tables)
            )
        )
    yield


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
