"""The one-off SQLite -> PostgreSQL copy (scripts/migrate_sqlite_to_postgres.py)."""

import sqlite3
from datetime import datetime

import pytest

from app import db
from scripts import migrate_sqlite_to_postgres as script


def _legacy_sqlite(path):
    """A small file shaped like the old SQLite schema, with some messy rows."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
            password_hash TEXT, google_sub TEXT, avatar_url TEXT,
            adult_mode INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            memory_enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE sessions (
            token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE chats (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'chat', subject TEXT, active_leaf_uid TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, uid TEXT,
            parent_uid TEXT, branch_index INTEGER NOT NULL DEFAULT 0, role TEXT NOT NULL,
            content TEXT NOT NULL, reasoning TEXT NOT NULL DEFAULT '',
            tools TEXT NOT NULL DEFAULT '[]', parts TEXT NOT NULL DEFAULT '[]',
            thinking_done INTEGER NOT NULL DEFAULT 0, thought_seconds REAL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content TEXT NOT NULL,
            source_chat_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE google_grants (
            user_id TEXT NOT NULL, service TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT '',
            access_token TEXT NOT NULL DEFAULT '', refresh_token TEXT NOT NULL DEFAULT '',
            expires_at TEXT, granted_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, service)
        );

        INSERT INTO users VALUES ('u1', 'a@b.com', 'A', NULL, 'g1', NULL, 1,
            '2024-05-01T10:00:00+00:00', 0);
        INSERT INTO users VALUES ('u2', 'c@d.com', 'C', NULL, NULL, NULL, 0,
            '2024-05-02T10:00:00', 1);
        INSERT INTO sessions VALUES ('t1', 'u1', '2024-05-01T10:00:00+00:00',
            '2999-01-01T00:00:00+00:00');
        INSERT INTO sessions VALUES ('t-orphan', 'ghost', '2024-05-01T10:00:00+00:00',
            '2999-01-01T00:00:00+00:00');
        INSERT INTO chats VALUES ('c1', 'u1', 'Hello', 'chat', NULL, 'a1',
            '2024-05-01T10:00:00+00:00', '2024-05-01T10:05:00+00:00');
        INSERT INTO messages (id, chat_id, uid, parent_uid, role, content, tools, parts,
            thinking_done, thought_seconds, created_at)
            VALUES (7, 'c1', 'u1m', NULL, 'user', 'hi', '[]', '[]', 0, NULL,
            '2024-05-01T10:00:01+00:00');
        INSERT INTO messages (id, chat_id, uid, parent_uid, role, content, tools, parts,
            thinking_done, thought_seconds, created_at)
            VALUES (9, 'c1', 'a1', 'u1m', 'assistant', 'yo', '[{"name": "x"}]',
            '[{"type": "text", "text": "yo"}]', 1, 2.5, '2024-05-01T10:00:02+00:00');
        INSERT INTO messages (id, chat_id, role, content, tools, parts, created_at)
            VALUES (11, 'c1', 'user', 'no uid, broken json', 'not json', '', 'garbage');
        INSERT INTO messages (id, chat_id, role, content, created_at)
            VALUES (12, 'ghost-chat', 'user', 'orphan', '2024-05-01T10:00:00+00:00');
        INSERT INTO memories VALUES ('m1', 'u1', 'Likes cats', 'c-deleted',
            '2024-05-01T10:00:00+00:00', '2024-05-01T10:00:00+00:00');
        INSERT INTO google_grants VALUES ('u1', 'gmail', 's', 'at', 'rt',
            '2030-01-01T00:00:00+00:00', '2024-05-01T10:00:00+00:00',
            '2024-05-01T10:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()


def test_migrates_rows_types_and_identity(isolated_db, tmp_path):
    source = tmp_path / "legacy.db"
    _legacy_sqlite(source)

    report = script.migrate(source, log=lambda _line: None)

    assert report["users"] == {"sqlite": 2, "postgres": 2, "skipped": 0}
    assert report["sessions"] == {"sqlite": 2, "postgres": 1, "skipped": 1}
    assert report["messages"] == {"sqlite": 4, "postgres": 3, "skipped": 1}
    # Tables missing from the old file are simply empty.
    assert report["webpage_sites"] == {"sqlite": 0, "postgres": 0, "skipped": 0}

    u1 = db.get_user_by_id("u1")
    assert u1["adult_mode"] is True and u1["memory_enabled"] is False
    assert datetime.fromisoformat(u1["created_at"]).year == 2024
    # A naive legacy timestamp is read as UTC.
    assert db.get_user_by_id("u2")["created_at"].endswith("+00:00")
    assert db.get_user_for_session("t1")["id"] == "u1"

    messages = db.list_messages_by_chat(["c1"])["c1"]
    assert [m["content"] for m in messages] == ["hi", "yo", "no uid, broken json"]
    assert messages[1]["tools"] == [{"name": "x"}]
    assert messages[1]["thinkingDone"] is True and messages[1]["thoughtSeconds"] == 2.5
    # Legacy row without uid is chained to the previous message; bad JSON -> [].
    assert messages[2]["id"] == "m11" and messages[2]["parentId"] == "a1"
    assert messages[2]["tools"] == [] and messages[2]["parts"] == []

    grant = db.get_google_grant("u1", "gmail")
    assert datetime.fromisoformat(grant["expires_at"]).year == 2030
    assert db.count_memories("u1") == 1  # source_chat_id has no foreign key

    # Copied ids are kept and new inserts continue after the highest one.
    db.append_chat_message("c1", "user", "after", uid="new")
    with db.connection() as conn:
        newest = conn.execute("SELECT MAX(id) AS n FROM messages").fetchone()["n"]
    assert newest > 11


def test_refuses_a_non_empty_target_unless_forced(isolated_db, tmp_path):
    source = tmp_path / "legacy.db"
    _legacy_sqlite(source)
    script.migrate(source, log=lambda _line: None)

    with pytest.raises(SystemExit, match="not empty"):
        script.migrate(source, log=lambda _line: None)
    assert db.get_user_by_id("u1") is not None  # nothing was lost

    report = script.migrate(source, force_truncate=True, log=lambda _line: None)
    assert report["users"]["postgres"] == 2
