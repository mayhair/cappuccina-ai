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
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "cappuccina.db"

# Files are fanned out into 256 buckets named by the first two hex characters
# of their UUID, so no single directory accumulates an unbounded file count.
def _shard(file_id: str) -> Path:
    return Path(file_id[:2])


# Generated image bytes live here as {shard}/{image_id}.{ext}; the images table
# holds the metadata. Derived from DATA_DIR at call time so tests that repoint
# DATA_DIR get an isolated images directory too.
def images_dir() -> Path:
    return DATA_DIR / "images"


# Only these MIME types are ever served; anything else is stored as .png.
_IMAGE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


def image_file_path(image_id: str, mime: str) -> Path:
    return images_dir() / _shard(image_id) / f"{image_id}.{_IMAGE_EXTENSIONS.get(mime, 'png')}"


# Uploaded attachments (user files) live here as {shard}/{attachment_id}.{ext}.
# The original filename is kept in the attachments table for display only — it
# is never used to build a path, so it can't escape this directory.
def attachments_dir() -> Path:
    return DATA_DIR / "attachments"


def attachment_file_path(attachment_id: str, filename: str) -> Path:
    return attachments_dir() / _shard(attachment_id) / f"{attachment_id}{Path(filename or '').suffix.lower()}"


# Write bytes, creating the shard directory on first use.
def _write_sharded(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


SESSION_TTL = timedelta(days=30)

# One connection is shared by the event loop and by the worker threads that run
# sync tools (LangChain runs them in an executor, and the tool node gathers
# several calls from one model message). CPython's sqlite3 does not support
# concurrent use of one connection: two threads preparing or stepping at the
# same time make SQLite answer SQLITE_MISUSE ("bad parameter or other API
# misuse"), and two threads creating the connection at the same time leave
# separate handles, one of them without the schema ("no such table", "database
# is locked"). Every statement therefore runs under this one reentrant lock,
# and sequences that must not interleave (site get-or-create, the message-tree
# swap) hold it for their whole length.
_conn_lock = threading.RLock()
_conn: _LockedConnection | None = None


class _StatementResult:
    """The rows of one finished statement, fetched while the lock was held.

    Callers only ever read rows, ``rowcount`` or ``lastrowid``, so a plain
    snapshot is enough — and it keeps the live cursor out of other threads'
    hands.
    """

    __slots__ = ("_rows", "_index", "rowcount", "lastrowid")

    def __init__(self, cursor: sqlite3.Cursor):
        self._rows = cursor.fetchall()
        self._index = 0
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid

    def fetchone(self) -> sqlite3.Row | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[sqlite3.Row]:
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


class _LockedConnection:
    """Serializes every use of the shared sqlite connection (see _conn_lock)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, sql: str, parameters: Any = ()) -> _StatementResult:
        with _conn_lock:
            return _StatementResult(self._conn.execute(sql, parameters))

    def executemany(self, sql: str, parameters: Any) -> _StatementResult:
        with _conn_lock:
            return _StatementResult(self._conn.executemany(sql, parameters))

    def executescript(self, sql: str) -> _StatementResult:
        with _conn_lock:
            return _StatementResult(self._conn.executescript(sql))

    def commit(self) -> None:
        with _conn_lock:
            self._conn.commit()

    def rollback(self) -> None:
        with _conn_lock:
            self._conn.rollback()

    def close(self) -> None:
        with _conn_lock:
            self._conn.close()


def get_conn() -> _LockedConnection:
    global _conn
    if _conn is None:
        with _conn_lock:
            if _conn is None:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                raw = sqlite3.connect(DB_PATH, check_same_thread=False)
                raw.row_factory = sqlite3.Row
                raw.execute("PRAGMA journal_mode=WAL")
                raw.execute("PRAGMA foreign_keys=ON")
                _init_schema(raw)
                _conn = _LockedConnection(raw)
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
            active_leaf_uid TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            uid TEXT,
            parent_uid TEXT,
            branch_index INTEGER NOT NULL DEFAULT 0,
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
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            service TEXT NOT NULL,
            scopes TEXT NOT NULL DEFAULT '',
            access_token TEXT NOT NULL DEFAULT '',
            refresh_token TEXT NOT NULL DEFAULT '',
            expires_at TEXT,
            granted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, service)
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

        CREATE TABLE IF NOT EXISTS deleted_events (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            event_id TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            PRIMARY KEY (user_id, event_id)
        );

        CREATE TABLE IF NOT EXISTS images (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            mime TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            width INTEGER,
            height INTEGER,
            prompt TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attachments (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            mime TEXT NOT NULL,
            kind TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            text TEXT NOT NULL DEFAULT '',
            char_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS webpage_sites (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chat_id TEXT NOT NULL UNIQUE REFERENCES chats(id) ON DELETE CASCADE,
            preview_key TEXT NOT NULL,
            active_commit TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
        CREATE INDEX IF NOT EXISTS idx_images_chat ON images(chat_id);
        CREATE INDEX IF NOT EXISTS idx_images_user ON images(user_id);
        CREATE INDEX IF NOT EXISTS idx_attachments_chat ON attachments(chat_id);
        CREATE INDEX IF NOT EXISTS idx_attachments_user ON attachments(user_id);
        """
    )
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "parts" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN parts TEXT NOT NULL DEFAULT '[]'")
    if "thinking_done" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN thinking_done INTEGER NOT NULL DEFAULT 0")
    if "thought_seconds" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN thought_seconds REAL")
    if "uid" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN uid TEXT")
    if "parent_uid" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN parent_uid TEXT")
    if "branch_index" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN branch_index INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_chat_uid ON messages(chat_id, uid)"
    )
    chat_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chats)").fetchall()}
    if "active_leaf_uid" not in chat_cols:
        conn.execute("ALTER TABLE chats ADD COLUMN active_leaf_uid TEXT")
    # The webpage canvas moved from JSON/BLOB rows in SQLite to a real folder on
    # disk (Step 1 of the container remake). Legacy rows describe pages that the
    # new workspace never reads, so this is an unconditional fresh start rather
    # than a migration; children first, else the implicit DELETE trips the
    # foreign keys.
    conn.execute("DROP TABLE IF EXISTS webpage_versions")
    conn.execute("DROP TABLE IF EXISTS webpage_assets")
    conn.execute("DROP TABLE IF EXISTS webpage_envs")
    _migrate_messages_for_branching(conn)
    _migrate_users_for_oauth(conn)
    _migrate_grants_for_services(conn)
    conn.commit()


def _migrate_messages_for_branching(conn: sqlite3.Connection) -> None:
    """Backfill message uids and parent links for pre-branching databases.

    Legacy messages form a single linear branch per chat: each row is linked to
    the previous row and given a stable uid derived from its rowid, so existing
    conversations survive the tree migration unchanged.
    """
    rows = conn.execute(
        "SELECT id, chat_id FROM messages WHERE uid IS NULL ORDER BY chat_id, id"
    ).fetchall()
    if not rows:
        return
    prev_chat = None
    prev_uid = None
    for row in rows:
        if row["chat_id"] != prev_chat:
            prev_chat = row["chat_id"]
            prev_uid = None
        uid = f"m{row['id']}"
        conn.execute(
            "UPDATE messages SET uid = :uid, parent_uid = :parent_uid,"
            " branch_index = 0 WHERE id = :id",
            {"uid": uid, "parent_uid": prev_uid, "id": row["id"]},
        )
        prev_uid = uid


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


def _migrate_grants_for_services(conn: sqlite3.Connection) -> None:
    """One-time rebuild letting a user hold one grant per Google service.

    Older databases stored a single grant row keyed by user_id (Gmail only).
    SQLite cannot change a primary key in place, so this runs the same
    create-copy-drop-rename dance as the users migration. Existing rows become
    service='gmail'.
    """
    info = {row["name"]: row for row in conn.execute("PRAGMA table_info(google_grants)").fetchall()}
    if not info or "service" in info:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        """
        CREATE TABLE google_grants_new (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            service TEXT NOT NULL,
            scopes TEXT NOT NULL DEFAULT '',
            access_token TEXT NOT NULL DEFAULT '',
            refresh_token TEXT NOT NULL DEFAULT '',
            expires_at TEXT,
            granted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, service)
        );
        INSERT INTO google_grants_new
            (user_id, service, scopes, access_token, refresh_token, expires_at, granted_at, updated_at)
            SELECT user_id, 'gmail', scopes, access_token, refresh_token, expires_at, granted_at, updated_at
            FROM google_grants;
        DROP TABLE google_grants;
        ALTER TABLE google_grants_new RENAME TO google_grants;
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


# ---------- Google grants (per-service OAuth tokens) ----------

def save_google_grant(
    user_id: str,
    service: str,
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
            (user_id, service, scopes, access_token, refresh_token, expires_at, granted_at, updated_at)
        VALUES
            (:user_id, :service, :scopes, :access_token, :refresh_token, :expires_at, :now, :now)
        ON CONFLICT(user_id, service) DO UPDATE SET
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
            "service": service,
            "scopes": scopes,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "now": now,
        },
    )
    conn.commit()


def get_google_grant(user_id: str, service: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM google_grants WHERE user_id = :user_id AND service = :service",
        {"user_id": user_id, "service": service},
    ).fetchone()


def delete_google_grant(user_id: str, service: str) -> None:
    conn = get_conn()
    conn.execute(
        "DELETE FROM google_grants WHERE user_id = :user_id AND service = :service",
        {"user_id": user_id, "service": service},
    )
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


# ---------- user-confirmed calendar deletions ----------

def mark_event_deleted(user_id: str, event_id: str) -> None:
    """Record that a proposed deletion was confirmed, so the card stays done
    after a reload (mirrors sent_drafts for email)."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO deleted_events (user_id, event_id, deleted_at)"
        " VALUES (:user_id, :event_id, :deleted_at)"
        " ON CONFLICT(user_id, event_id) DO UPDATE SET deleted_at = :deleted_at",
        {"user_id": user_id, "event_id": event_id, "deleted_at": _now()},
    )
    conn.commit()


def get_deleted_event_ids(user_id: str) -> set[str]:
    rows = get_conn().execute(
        "SELECT event_id FROM deleted_events WHERE user_id = :user_id",
        {"user_id": user_id},
    ).fetchall()
    return {row["event_id"] for row in rows}


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
            "SELECT id, title, mode, subject,"
            " active_leaf_uid AS activeLeafId FROM chats"
            " WHERE user_id = :user_id ORDER BY created_at DESC",
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
        "SELECT chat_id, uid, parent_uid, branch_index, role, content, reasoning, tools,"
        " parts, thinking_done, thought_seconds FROM messages"
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
                "id": r["uid"],
                "parentId": r["parent_uid"],
                "branchIndex": r["branch_index"] or 0,
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


def set_chat_active_leaf(user_id: str, chat_id: str, leaf_uid: str | None) -> bool:
    """Remember which branch tail the user is viewing, so it survives reloads
    and follows them across devices."""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE chats SET active_leaf_uid = :leaf, updated_at = :now"
        " WHERE id = :id AND user_id = :user_id",
        {"leaf": leaf_uid, "now": _now(), "id": chat_id, "user_id": user_id},
    )
    conn.commit()
    return cur.rowcount > 0


def delete_chat(user_id: str, chat_id: str) -> bool:
    conn = get_conn()
    doomed = conn.execute(
        "SELECT id, mime FROM images WHERE chat_id = :chat_id AND user_id = :user_id",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchall()
    doomed_attachments = conn.execute(
        "SELECT id, filename FROM attachments"
        " WHERE chat_id = :chat_id AND user_id = :user_id",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchall()
    cur = conn.execute(
        "DELETE FROM chats WHERE id = :id AND user_id = :user_id",
        {"id": chat_id, "user_id": user_id},
    )
    conn.commit()
    if cur.rowcount > 0:
        # Image/attachment rows go with the chat via ON DELETE CASCADE; the
        # files need explicit cleanup so deleted chats leave no orphan bytes.
        for row in doomed:
            try:
                image_file_path(row["id"], row["mime"]).unlink(missing_ok=True)
            except OSError:
                pass
        for row in doomed_attachments:
            try:
                attachment_file_path(row["id"], row["filename"]).unlink(missing_ok=True)
            except OSError:
                pass
    return cur.rowcount > 0


def replace_chat_messages(chat_id: str, messages: list[dict]) -> None:
    """Full-tree sync: replaces stored messages with the given tree.

    The client is treated as authoritative for message structure during this
    transition; each node carries its uid, parent link and sibling order. The
    assistant reply is appended by append_chat_message(). The swap commits as
    one transaction under the connection lock, so a concurrent read (the
    sidebar refreshing, another device syncing) can never catch the tree
    half-replaced.
    """
    with _conn_lock:
        conn = get_conn()
        conn.execute("DELETE FROM messages WHERE chat_id = :chat_id", {"chat_id": chat_id})
        conn.executemany(
            "INSERT INTO messages (chat_id, uid, parent_uid, branch_index, role, content,"
            " reasoning, tools, parts, thinking_done, thought_seconds, created_at)"
            " VALUES (:chat_id, :uid, :parent_uid, :branch_index, :role, :content,"
            " :reasoning, :tools, :parts, :thinking_done, :thought_seconds, :created_at)",
            [
                {
                    "chat_id": chat_id,
                    "uid": m.get("uid"),
                    "parent_uid": m.get("parent_uid"),
                    "branch_index": m.get("branch_index", 0) or 0,
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
        conn.commit()


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
                        thought_seconds: float | None = None,
                        uid: str | None = None,
                        parent_uid: str | None = None) -> None:
    """Append a message to the tree, deriving its sibling order when parented.

    ``branch_index`` follows the same max+1 rule the client uses, so the two
    stay in lockstep without the client having to precompute it.
    """
    conn = get_conn()
    if parent_uid is None:
        branch_index = 0
    else:
        row = conn.execute(
            "SELECT MAX(branch_index) AS mx FROM messages"
            " WHERE chat_id = :chat_id AND parent_uid = :parent_uid",
            {"chat_id": chat_id, "parent_uid": parent_uid},
        ).fetchone()
        branch_index = (row["mx"] + 1) if row and row["mx"] is not None else 0
    conn.execute(
        "INSERT INTO messages (chat_id, uid, parent_uid, branch_index, role, content,"
        " reasoning, tools, parts, thinking_done, thought_seconds, created_at)"
        " VALUES (:chat_id, :uid, :parent_uid, :branch_index, :role, :content,"
        " :reasoning, :tools, :parts, :thinking_done, :thought_seconds, :created_at)",
        {
            "chat_id": chat_id,
            "uid": uid,
            "parent_uid": parent_uid,
            "branch_index": branch_index,
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


# ---------- generated images ----------

def create_image(
    user_id: str,
    chat_id: str,
    data: bytes,
    mime: str = "image/png",
    width: int | None = None,
    height: int | None = None,
    prompt: str = "",
) -> dict:
    """Persist generated image bytes and return the metadata row as a dict.

    Image bytes are the server's source of truth: messages only carry a small
    {"type": "image", "imageId": ...} part, so base64 never flows through the
    full-history sync payloads.
    """
    image_id = uuid.uuid4().hex
    _write_sharded(image_file_path(image_id, mime), data)
    conn = get_conn()
    conn.execute(
        "INSERT INTO images (id, user_id, chat_id, mime, size_bytes, width,"
        " height, prompt, created_at)"
        " VALUES (:id, :user_id, :chat_id, :mime, :size_bytes, :width,"
        " :height, :prompt, :created_at)",
        {
            "id": image_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "mime": mime,
            "size_bytes": len(data),
            "width": width,
            "height": height,
            "prompt": prompt,
            "created_at": _now(),
        },
    )
    conn.commit()
    created = get_image(user_id, image_id)
    assert created is not None
    return dict(created)


def get_image(user_id: str, image_id: str) -> sqlite3.Row | None:
    """User-scoped lookup: another user's image (or a bogus id) is a miss."""
    return get_conn().execute(
        "SELECT * FROM images WHERE id = :id AND user_id = :user_id",
        {"id": image_id, "user_id": user_id},
    ).fetchone()


# ---------- attachments (user uploads) ----------

def create_attachment(
    user_id: str,
    chat_id: str,
    filename: str,
    data: bytes,
    kind: str,
    mime: str,
    text: str = "",
) -> dict:
    """Persist an uploaded file and return its metadata row as a dict.

    Image bytes and document text are the server's source of truth: messages
    only carry a small {"type": "attachment", "attachmentId": ...} part, so
    neither bytes nor extracted text flow through full-history sync payloads.
    """
    attachment_id = uuid.uuid4().hex
    _write_sharded(attachment_file_path(attachment_id, filename), data)
    conn = get_conn()
    conn.execute(
        "INSERT INTO attachments (id, user_id, chat_id, filename, mime, kind,"
        " size_bytes, text, char_count, created_at)"
        " VALUES (:id, :user_id, :chat_id, :filename, :mime, :kind,"
        " :size_bytes, :text, :char_count, :created_at)",
        {
            "id": attachment_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "filename": filename,
            "mime": mime,
            "kind": kind,
            "size_bytes": len(data),
            "text": text,
            "char_count": len(text),
            "created_at": _now(),
        },
    )
    conn.commit()
    created = get_attachment(user_id, attachment_id)
    assert created is not None
    return dict(created)


def get_attachment(user_id: str, attachment_id: str) -> sqlite3.Row | None:
    """User-scoped lookup: another user's file (or a bogus id) is a miss."""
    return get_conn().execute(
        "SELECT * FROM attachments WHERE id = :id AND user_id = :user_id",
        {"id": attachment_id, "user_id": user_id},
    ).fetchone()


def delete_attachment(user_id: str, attachment_id: str) -> bool:
    conn = get_conn()
    row = get_attachment(user_id, attachment_id)
    if row is None:
        return False
    conn.execute(
        "DELETE FROM attachments WHERE id = :id AND user_id = :user_id",
        {"id": attachment_id, "user_id": user_id},
    )
    conn.commit()
    try:
        attachment_file_path(row["id"], row["filename"]).unlink(missing_ok=True)
    except OSError:
        pass
    return True


# ---------- webpage sites (the canvas) ----------

# The site row is only a registry: the page itself is a folder under
# backend/data/sites/{id} with a git history, and the container runtime in
# app/sitebox.py owns both. Keeping just the pointer here means SQLite never
# holds file bytes, so file size and history length are disk concerns.


def get_webpage_site(user_id: str, chat_id: str) -> dict | None:
    """The chat's website workspace, or None when the chat has none yet.

    User-scoped: another user's chat (or a bogus id) is a miss. `preview_key`
    is the capability token in the preview URL, and `active_commit` is the git
    commit the canvas currently serves (None before the first commit).
    """
    row = get_conn().execute(
        "SELECT * FROM webpage_sites WHERE chat_id = :chat_id AND user_id = :user_id",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchone()
    return dict(row) if row is not None else None


def get_webpage_site_by_chat(chat_id: str) -> dict | None:
    """The chat's website workspace without a user scope, or None.

    Only the capability-URL preview route uses this: that route has no session
    (module scripts inside the sandboxed iframe send cookies at best, and the
    iframe has an opaque origin), so the unguessable `preview_key` in its path
    is the authorization. Every user-facing route stays scoped by user_id.
    """
    row = get_conn().execute(
        "SELECT * FROM webpage_sites WHERE chat_id = :chat_id",
        {"chat_id": chat_id},
    ).fetchone()
    return dict(row) if row is not None else None


def create_webpage_site(user_id: str, chat_id: str, preview_key: str) -> dict:
    """Register the chat's workspace and return the row as a dict.

    The workspace folder and its git repo are created by the container layer;
    this only claims the row, so a crash between the two leaves an empty
    workspace rather than an unreachable page. `chat_id` is UNIQUE, so the
    caller checks get_webpage_site first (one workspace per chat).
    """
    site_id = uuid.uuid4().hex
    now = _now()
    conn = get_conn()
    conn.execute(
        "INSERT INTO webpage_sites (id, user_id, chat_id, preview_key, active_commit,"
        " created_at, updated_at)"
        " VALUES (:id, :user_id, :chat_id, :preview_key, NULL, :created_at, :updated_at)",
        {
            "id": site_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "preview_key": preview_key,
            "created_at": now,
            "updated_at": now,
        },
    )
    conn.commit()
    created = get_webpage_site(user_id, chat_id)
    assert created is not None
    return created


def get_or_create_webpage_site(user_id: str, chat_id: str, preview_key: str) -> dict:
    """The chat's site, created with ``preview_key`` on first use.

    Runs as one critical section: several tool calls of a single model message
    execute in parallel (the tool node gathers them), and every one of them
    ensures the site before its first write — a plain get-then-create would
    race two INSERTs into an IntegrityError that kills the whole run.
    """
    with _conn_lock:
        site = get_webpage_site(user_id, chat_id)
        if site is None:
            site = create_webpage_site(user_id, chat_id, preview_key)
        return site


def set_webpage_active_commit(user_id: str, chat_id: str, commit: str) -> None:
    """Point the canvas at one commit's snapshot (a checkout).

    Also bumps updated_at: switching what the preview serves is a change to the
    site, and touch_webpage_site's idle accounting should see it.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE webpage_sites SET active_commit = :commit, updated_at = :now"
        " WHERE chat_id = :chat_id AND user_id = :user_id",
        {"commit": commit, "now": _now(), "chat_id": chat_id, "user_id": user_id},
    )
    conn.commit()


def touch_webpage_site(user_id: str, chat_id: str) -> None:
    """Bump updated_at so the site's row tracks the workspace's last change."""
    conn = get_conn()
    conn.execute(
        "UPDATE webpage_sites SET updated_at = :now"
        " WHERE chat_id = :chat_id AND user_id = :user_id",
        {"now": _now(), "chat_id": chat_id, "user_id": user_id},
    )
    conn.commit()

