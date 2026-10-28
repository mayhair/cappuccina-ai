"""Sessions slide forward with use, end when idle, and never outlive the cap."""

from datetime import datetime, timedelta, timezone

from fastapi import Response

from app import db
from app.auth import set_session_cookie


def _user() -> dict:
    return db.create_google_user("sessions@example.com", "S", "sessions-sub")


def _set_times(token: str, *, created_ago: timedelta, expires_in: timedelta) -> None:
    now = datetime.now(timezone.utc)
    with db.connection() as conn:
        conn.execute(
            "UPDATE sessions SET created_at = %s, expires_at = %s WHERE token = %s",
            (now - created_ago, now + expires_in, token),
        )


def _expires_in(token: str) -> timedelta:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT expires_at - now() AS left FROM sessions WHERE token = %s", (token,)
        ).fetchone()
    return row["left"]


def test_fresh_session_is_not_rewritten(isolated_db):
    token = db.create_session(_user()["id"])
    before = _expires_in(token)
    assert db.get_user_for_session(token) is not None
    assert abs(_expires_in(token) - before) < timedelta(seconds=5)


def test_use_in_second_half_slides_expiry_forward(isolated_db):
    token = db.create_session(_user()["id"])
    _set_times(token, created_ago=timedelta(days=20), expires_in=timedelta(days=10))
    assert db.get_user_for_session(token) is not None
    assert _expires_in(token) > db.SESSION_TTL - timedelta(minutes=1)


def test_renewal_never_passes_the_absolute_cap(isolated_db):
    token = db.create_session(_user()["id"])
    _set_times(token, created_ago=timedelta(days=80), expires_in=timedelta(days=5))
    assert db.get_user_for_session(token) is not None
    left = _expires_in(token)
    assert timedelta(days=9, hours=23) < left <= timedelta(days=10)


def test_session_past_the_cap_is_rejected(isolated_db):
    token = db.create_session(_user()["id"])
    _set_times(token, created_ago=timedelta(days=90), expires_in=timedelta(seconds=-1))
    assert db.get_user_for_session(token) is None


def test_idle_session_is_rejected(isolated_db):
    token = db.create_session(_user()["id"])
    _set_times(token, created_ago=timedelta(days=31), expires_in=timedelta(seconds=-1))
    assert db.get_user_for_session(token) is None


def test_cookie_lasts_for_the_absolute_cap():
    response = Response()
    set_session_cookie(response, "tok")
    assert f"Max-Age={90 * 24 * 3600}" in response.headers["set-cookie"]
