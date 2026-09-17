"""SQLite storage for accounts, sessions and chat history.

The DB lives in backend/data/cappuccina.db (gitignored). Chat storage is
server-side only: the client mirrors what is stored here.
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "cappuccina.db"

_session_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SESSION_TTL = timedelta(days=30)


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT,
            google_sub TEXT,
            avatar_url TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'chat',
            subject TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            reasoning TEXT NOT NULL DEFAULT '',
            tools TEXT NOT NULL DEFAULT '[]',
            parts TEXT NOT NULL DEFAULT '[]',
            thinking_done INTEGER NOT NULL DEFAULT 0,
            thought_seconds REAL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS google_grants (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            scopes TEXT NOT NULL DEFAULT '',
            access_token TEXT NOT NULL DEFAULT '',
            refresh_token TEXT NOT NULL DEFAULT '',
            expires_at TEXT,
            granted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS oauth_states (
            token TEXT PRIMARY KEY,
            purpose TEXT NOT NULL,
            user_id TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sent_drafts (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            draft_id TEXT NOT NULL,
            message_id TEXT NOT NULL DEFAULT '',
            sent_at TEXT NOT NULL,
            PRIMARY KEY (user_id, draft_id)
        );

        CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
        """
    )
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "parts" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN parts TEXT NOT NULL DEFAULT '[]'")
    if "thinking_done" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN thinking_done INTEGER NOT NULL DEFAULT 0")
    if "thought_seconds" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN thought_seconds REAL")
    _migrate_users_for_oauth(conn)
    conn.commit()


def _migrate_users_for_oauth(conn: sqlite3.Connection) -> None:
    """One-time rebuild making password_hash nullable and adding OAuth columns.

    SQLite cannot drop a NOT NULL constraint in place, so legacy databases get
    the documented create-copy-drop-rename dance. Chats/sessions survive because
    their foreign keys resolve the ``users`` name at query time.
    """
    info = {row["name"]: row for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if info and info.get("password_hash") and info["password_hash"]["notnull"] == 0:
        # Already migrated: just backfill any missing OAuth columns.
        for name, decl in (("google_sub", "TEXT"), ("avatar_url", "TEXT")):
            if name not in info:
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub)")
        return
    if not info:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub)")
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        """
        CREATE TABLE users_new (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT,
            google_sub TEXT,
            avatar_url TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO users_new (id, email, display_name, password_hash, google_sub, avatar_url, created_at)
            SELECT id, email, display_name, password_hash, NULL, NULL, created_at FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub);
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- users ----------

def create_google_user(
    email: str, display_name: str, google_sub: str, avatar_url: str | None = None
) -> sqlite3.Row:
    row = {
        "id": uuid.uuid4().hex,
        "email": email,
        "display_name": display_name,
        "google_sub": google_sub,
        "avatar_url": avatar_url,
        "created_at": _now(),
    }
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (id, email, display_name, google_sub, avatar_url, created_at)"
        " VALUES (:id, :email, :display_name, :google_sub, :avatar_url, :created_at)",
        row,
    )
    conn.commit()
    created = get_user_by_id(row["id"])
    assert created is not None
    return created


def get_user_by_email(email: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM users WHERE email = :email", {"email": email}
    ).fetchone()


def get_user_by_google_sub(google_sub: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM users WHERE google_sub = :sub", {"sub": google_sub}
    ).fetchone()


def get_user_by_id(user_id: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM users WHERE id = :id", {"id": user_id}
    ).fetchone()


def link_google_account(
    user_id: str, google_sub: str, avatar_url: str | None = None
) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE users SET google_sub = :sub, avatar_url = :avatar WHERE id = :id",
        {"sub": google_sub, "avatar": avatar_url, "id": user_id},
    )
    conn.commit()


def update_user_display_name(user_id: str, display_name: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE users SET display_name = :name WHERE id = :id",
        {"name": display_name, "id": user_id},
    )
    conn.commit()


# ---------- Google grants (Gmail tokens) ----------

def save_google_grant(
    user_id: str,
    scopes: str,
    access_token: str,
    refresh_token: str,
    expires_at: str | None,
) -> None:
    now = _now()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO google_grants
            (user_id, scopes, access_token, refresh_token, expires_at, granted_at, updated_at)
        VALUES
            (:user_id, :scopes, :access_token, :refresh_token, :expires_at, :now, :now)
        ON CONFLICT(user_id) DO UPDATE SET
            scopes = :scopes,
            access_token = :access_token,
            refresh_token = CASE
                WHEN :refresh_token = '' THEN google_grants.refresh_token
                ELSE :refresh_token
            END,
            expires_at = :expires_at,
            updated_at = :now
        """,
        {
            "user_id": user_id,
            "scopes": scopes,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "now": now,
        },
    )
    conn.commit()


def get_google_grant(user_id: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM google_grants WHERE user_id = :user_id", {"user_id": user_id}
    ).fetchone()


def delete_google_grant(user_id: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM google_grants WHERE user_id = :user_id", {"user_id": user_id})
    conn.commit()


# ---------- sent Gmail drafts ----------

def mark_draft_sent(user_id: str, draft_id: str, message_id: str = "") -> None:
    """Record that a draft was actually sent so the UI can stop offering Send."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO sent_drafts (user_id, draft_id, message_id, sent_at)"
        " VALUES (:user_id, :draft_id, :message_id, :sent_at)"
        " ON CONFLICT(user_id, draft_id) DO UPDATE SET"
        " message_id = :message_id, sent_at = :sent_at",
        {
            "user_id": user_id,
            "draft_id": draft_id,
            "message_id": message_id,
            "sent_at": _now(),
        },
    )
    conn.commit()


def get_sent_draft_ids(user_id: str) -> set[str]:
    rows = get_conn().execute(
        "SELECT draft_id FROM sent_drafts WHERE user_id = :user_id",
        {"user_id": user_id},
    ).fetchall()
    return {row["draft_id"] for row in rows}


# ---------- OAuth state (CSRF protection) ----------

def create_oauth_state(purpose: str, user_id: str | None = None) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex
    conn = get_conn()
    conn.execute("DELETE FROM oauth_states WHERE expires_at < :now", {"now": _now()})
    conn.execute(
        "INSERT INTO oauth_states (token, purpose, user_id, created_at, expires_at)"
        " VALUES (:token, :purpose, :user_id, :created_at, :expires_at)",
        {
            "token": token,
            "purpose": purpose,
            "user_id": user_id,
            "created_at": _now(),
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat(),
        },
    )
    conn.commit()
    return token


def consume_oauth_state(token: str, purpose: str) -> sqlite3.Row | None:
    """Fetch and delete a state token in one shot; returns None if invalid."""
    if not token:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM oauth_states WHERE token = :token AND purpose = :purpose",
        {"token": token, "purpose": purpose},
    ).fetchone()
    conn.execute("DELETE FROM oauth_states WHERE token = :token", {"token": token})
    conn.commit()
    if row is None or row["expires_at"] <= _now():
        return None
    return row


# ---------- sessions ----------

def create_session(user_id: str) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex
    with _session_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at)"
            " VALUES (:token, :user_id, :created_at, :expires_at)",
            {
                "token": token,
                "user_id": user_id,
                "created_at": _now(),
                "expires_at": (datetime.now(timezone.utc) + SESSION_TTL)
                .isoformat(),
            },
        )
        conn.commit()
    return token


def get_user_for_session(token: str) -> sqlite3.Row | None:
    if not token:
        return None
    row = get_conn().execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token = :token AND s.expires_at > :now",
        {"token": token, "now": _now()},
    ).fetchone()
    return row


def delete_session(token: str) -> None:
    with _session_lock:
        conn = get_conn()
        conn.execute("DELETE FROM sessions WHERE token = :token", {"token": token})
        conn.commit()


# ---------- chats ----------

def create_chat(user_id: str, title: str = "New chat", mode: str = "chat",
                subject: str | None = None, chat_id: str | None = None) -> dict:
    now = _now()
    row = {
        "id": chat_id or uuid.uuid4().hex,
        "user_id": user_id,
        "title": title,
        "mode": mode,
        "subject": subject,
        "created_at": now,
        "updated_at": now,
    }
    conn = get_conn()
    conn.execute(
        "INSERT INTO chats (id, user_id, title, mode, subject, created_at, updated_at)"
        " VALUES (:id, :user_id, :title, :mode, :subject, :created_at, :updated_at)",
        row,
    )
    conn.commit()
    return {k: row[k] for k in ("id", "title", "mode", "subject")}


def get_chat(user_id: str, chat_id: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM chats WHERE id = :id AND user_id = :user_id",
        {"id": chat_id, "user_id": user_id},
    ).fetchone()


def list_chats(user_id: str) -> list[dict]:
    conn = get_conn()
    chats = [
        dict(r)
        for r in conn.execute(
            "SELECT id, title, mode, subject FROM chats WHERE user_id = :user_id"
            " ORDER BY created_at DESC",
            {"user_id": user_id},
        ).fetchall()
    ]
    messages_grouped = list_messages_by_chat([c["id"] for c in chats])
    for chat in chats:
        chat["messages"] = messages_grouped.get(chat["id"], [])
    return chats


def list_messages_by_chat(chat_ids: list[str]) -> dict[str, list[dict]]:
    if not chat_ids:
        return {}
    conn = get_conn()
    placeholders = ",".join("?" for _ in chat_ids)
    rows = conn.execute(
        "SELECT chat_id, role, content, reasoning, tools, parts,"
        " thinking_done, thought_seconds FROM messages"
        f" WHERE chat_id IN ({placeholders}) ORDER BY id",
        chat_ids,
    ).fetchall()
    grouped: dict[str, list[dict]] = {cid: [] for cid in chat_ids}
    for r in rows:
        try:
            parts = json.loads(r["parts"]) if r["parts"] else []
        except (ValueError, TypeError):
            parts = []
        grouped[r["chat_id"]].append(
            {
                "role": r["role"],
                "content": r["content"],
                "reasoning": r["reasoning"],
                "tools": json.loads(r["tools"]),
                "parts": parts,
                "thinkingDone": bool(r["thinking_done"]),
                "thoughtSeconds": r["thought_seconds"],
            }
        )
    return grouped


def rename_chat(user_id: str, chat_id: str, title: str) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE chats SET title = :title, updated_at = :now"
        " WHERE id = :id AND user_id = :user_id",
        {"title": title, "now": _now(), "id": chat_id, "user_id": user_id},
    )
    conn.commit()
    return cur.rowcount > 0


def delete_chat(user_id: str, chat_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM chats WHERE id = :id AND user_id = :user_id",
        {"id": chat_id, "user_id": user_id},
    )
    conn.commit()
    return cur.rowcount > 0


def replace_chat_messages(chat_id: str, messages: list[dict]) -> None:
    """Full-history sync: replaces stored messages with the given history.

    The client is treated as authoritative for message order during this
    transition; the assistant reply is appended by append_message().
    """
    conn = get_conn()
    conn.execute("DELETE FROM messages WHERE chat_id = :chat_id", {"chat_id": chat_id})
    conn.executemany(
        "INSERT INTO messages (chat_id, role, content, reasoning, tools, parts,"
        " thinking_done, thought_seconds, created_at)"
        " VALUES (:chat_id, :role, :content, :reasoning, :tools, :parts,"
        " :thinking_done, :thought_seconds, :created_at)",
        [
            {
                "chat_id": chat_id,
                "role": m["role"],
                "content": m["content"],
                "reasoning": m.get("reasoning", ""),
                "tools": json.dumps(m.get("tools", [])),
                "parts": json.dumps(m.get("parts", [])),
                "thinking_done": 1 if m.get("thinking_done", False) else 0,
                "thought_seconds": m.get("thought_seconds"),
                "created_at": _now(),
            }
            for m in messages
        ],
    )
    conn.execute(
        "UPDATE chats SET updated_at = :now WHERE id = :chat_id",
        {"now": _now(), "chat_id": chat_id},
    )


def update_chat_meta(chat_id: str, mode: str | None = None,
                     subject: str | None = None,
                     clear_subject: bool = False) -> None:
    sets = ["updated_at = :now"]
    params: dict = {"now": _now(), "chat_id": chat_id}
    if mode is not None:
        sets.append("mode = :mode")
        params["mode"] = mode
    if clear_subject:
        sets.append("subject = NULL")
    elif subject is not None:
        sets.append("subject = :subject")
        params["subject"] = subject
    conn = get_conn()
    conn.execute(
        f"UPDATE chats SET {', '.join(sets)} WHERE id = :chat_id", params
    )
    conn.commit()


def append_chat_message(chat_id: str, role: str, content: str,
                        reasoning: str = "", tools: list | None = None,
                        parts: list | None = None,
                        thinking_done: bool = False,
                        thought_seconds: float | None = None) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages (chat_id, role, content, reasoning, tools, parts,"
        " thinking_done, thought_seconds, created_at)"
        " VALUES (:chat_id, :role, :content, :reasoning, :tools, :parts,"
        " :thinking_done, :thought_seconds, :created_at)",
        {
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "reasoning": reasoning,
            "tools": json.dumps(tools or []),
            "parts": json.dumps(parts or []),
            "thinking_done": 1 if thinking_done else 0,
            "thought_seconds": thought_seconds,
            "created_at": _now(),
        },
    )
    conn.execute(
        "UPDATE chats SET updated_at = :now WHERE id = :chat_id",
        {"now": _now(), "chat_id": chat_id},
    )
    conn.commit()


def set_chat_title_if_default(chat_id: str, title: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE chats SET title = :title, updated_at = :now"
        " WHERE id = :chat_id AND title = 'New chat'",
        {"title": title, "now": _now(), "chat_id": chat_id},
    )
    conn.commit()
