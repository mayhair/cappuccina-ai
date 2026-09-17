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
            password_hash TEXT NOT NULL,
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
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
        """
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- users ----------

def create_user(email: str, display_name: str, password_hash: str) -> dict:
    row = {
        "id": uuid.uuid4().hex,
        "email": email,
        "display_name": display_name,
        "password_hash": password_hash,
        "created_at": _now(),
    }
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (id, email, display_name, password_hash, created_at)"
        " VALUES (:id, :email, :display_name, :password_hash, :created_at)",
        row,
    )
    conn.commit()
    return {k: row[k] for k in ("id", "email", "display_name")}


def get_user_by_email(email: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM users WHERE email = :email", {"email": email}
    ).fetchone()


def get_user_by_id(user_id: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM users WHERE id = :id", {"id": user_id}
    ).fetchone()


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
        f"SELECT chat_id, role, content, reasoning, tools FROM messages"
        f" WHERE chat_id IN ({placeholders}) ORDER BY id",
        chat_ids,
    ).fetchall()
    grouped: dict[str, list[dict]] = {cid: [] for cid in chat_ids}
    for r in rows:
        grouped[r["chat_id"]].append(
            {
                "role": r["role"],
                "content": r["content"],
                "reasoning": r["reasoning"],
                "tools": json.loads(r["tools"]),
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
        "INSERT INTO messages (chat_id, role, content, reasoning, tools, created_at)"
        " VALUES (:chat_id, :role, :content, :reasoning, :tools, :created_at)",
        [
            {
                "chat_id": chat_id,
                "role": m["role"],
                "content": m["content"],
                "reasoning": m.get("reasoning", ""),
                "tools": json.dumps(m.get("tools", [])),
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
                        reasoning: str = "", tools: list | None = None) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages (chat_id, role, content, reasoning, tools, created_at)"
        " VALUES (:chat_id, :role, :content, :reasoning, :tools, :created_at)",
        {
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "reasoning": reasoning,
            "tools": json.dumps(tools or []),
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
