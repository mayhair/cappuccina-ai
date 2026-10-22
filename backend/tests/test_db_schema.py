"""Schema migrations and the row conversions at the db boundary."""

from datetime import datetime

from app import db


def test_migrations_are_recorded_and_reapplying_is_a_no_op(isolated_db):
    expected = [version for version, _name, _sql in db._load_migrations()]
    with db.connection() as conn:
        recorded = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert recorded == expected

    db._apply_migrations(db.get_pool())  # nothing pending, nothing breaks
    with db.connection() as conn:
        again = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert again == expected


def test_timestamps_come_back_as_iso_strings(isolated_db):
    user = db.create_google_user("iso@example.com", "Iso", "sub-iso")
    assert isinstance(user["created_at"], str)
    parsed = datetime.fromisoformat(user["created_at"])
    assert parsed.utcoffset().total_seconds() == 0


def test_flags_and_json_round_trip_as_python_values(isolated_db):
    user = db.create_google_user("types@example.com", "Types", "sub-types")
    assert user["adult_mode"] is False
    assert user["memory_enabled"] is True
    chat = db.create_chat(user["id"])
    db.append_chat_message(
        chat["id"], "assistant", "hi\x00there", tools=[{"name": "t", "n": 1}],
        parts=[{"type": "text", "text": "a\x00b"}], thinking_done=True,
        thought_seconds=1.5, uid="a1",
    )
    message = db.list_messages_by_chat([chat["id"]])[chat["id"]][0]
    assert message["content"] == "hithere"
    assert message["tools"] == [{"name": "t", "n": 1}]
    assert message["parts"] == [{"type": "text", "text": "ab"}]
    assert message["thinkingDone"] is True
    assert message["thoughtSeconds"] == 1.5


def test_a_gmail_and_a_calendar_grant_coexist_per_user(isolated_db):
    user = db.create_google_user("grants@example.com", "G", "sub-grants")
    db.save_google_grant(user["id"], "gmail", "gmail.readonly", "at", "rt", None)
    db.save_google_grant(
        user["id"], "calendar", "calendar.events", "c", "r", "2030-01-01T00:00:00+00:00"
    )
    assert db.get_google_grant(user["id"], "gmail")["scopes"] == "gmail.readonly"
    calendar = db.get_google_grant(user["id"], "calendar")
    assert datetime.fromisoformat(calendar["expires_at"]).year == 2030

    # A refresh response carries no refresh token: the stored one is kept.
    db.save_google_grant(user["id"], "gmail", "gmail.readonly", "at2", "", None)
    grant = db.get_google_grant(user["id"], "gmail")
    assert (grant["access_token"], grant["refresh_token"]) == ("at2", "rt")


def test_oauth_state_is_atomic_and_checks_purpose(isolated_db):
    state = db.create_oauth_state("login")
    assert db.consume_oauth_state(state, "gmail") is None  # wrong purpose burns it
    assert db.consume_oauth_state(state, "login") is None

    user = db.create_google_user("st@example.com", "S", "sub-st")
    state = db.create_oauth_state("gmail", user["id"])
    row = db.consume_oauth_state(state, "gmail")
    assert row is not None and row["user_id"] == user["id"]
