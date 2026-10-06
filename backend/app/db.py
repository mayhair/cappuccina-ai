"""SQLite storage for accounts, sessions and chat history.

The DB lives in backend/data/cappuccina.db (gitignored). Chat storage is
server-side only: the client mirrors what is stored here.
"""

import difflib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "cappuccina.db"

# Generated image bytes live here as {image_id}.{ext}; the images table holds
# the metadata. Derived from DATA_DIR at call time so tests that repoint
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
    return images_dir() / f"{image_id}.{_IMAGE_EXTENSIONS.get(mime, 'png')}"


# Uploaded attachments (user files) live here as {attachment_id}.{ext}. The
# original filename is kept in the attachments table for display only — it is
# never used to build a path, so it can't escape this directory.
def attachments_dir() -> Path:
    return DATA_DIR / "attachments"


def attachment_file_path(attachment_id: str, filename: str) -> Path:
    return attachments_dir() / f"{attachment_id}{Path(filename or '').suffix.lower()}"


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

        CREATE TABLE IF NOT EXISTS webpage_envs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chat_id TEXT NOT NULL UNIQUE REFERENCES chats(id) ON DELETE CASCADE,
            files TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS webpage_versions (
            id TEXT PRIMARY KEY,
            env_id TEXT NOT NULL REFERENCES webpage_envs(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            parent_version_id TEXT,
            seq INTEGER NOT NULL,
            origin TEXT NOT NULL,
            message_uid TEXT,
            label TEXT NOT NULL DEFAULT '',
            files TEXT NOT NULL DEFAULT '{}',
            assets TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS webpage_assets (
            id TEXT PRIMARY KEY,
            env_id TEXT NOT NULL REFERENCES webpage_envs(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            mime TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            data BLOB NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_webpage_assets_path
            ON webpage_assets(env_id, path);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_webpage_versions_seq ON webpage_versions(chat_id, seq);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_webpage_versions_uid ON webpage_versions(chat_id, message_uid) WHERE message_uid IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_webpage_versions_env ON webpage_versions(env_id, seq);

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
    env_cols = {row["name"] for row in conn.execute("PRAGMA table_info(webpage_envs)").fetchall()}
    if "active_version_id" not in env_cols:
        conn.execute("ALTER TABLE webpage_envs ADD COLUMN active_version_id TEXT")
    version_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(webpage_versions)").fetchall()
    }
    if "assets" not in version_cols:
        conn.execute("ALTER TABLE webpage_versions ADD COLUMN assets TEXT NOT NULL DEFAULT '[]'")
    _migrate_webpage_versions(conn)
    _migrate_messages_for_branching(conn)
    _migrate_users_for_oauth(conn)
    _migrate_grants_for_services(conn)
    conn.commit()


def _migrate_webpage_versions(conn: sqlite3.Connection) -> None:
    """Give every pre-existing webpage environment a first iteration.

    Chats built before iterations existed have only the working copy in
    webpage_envs; backfilling it as seq 1 makes the history non-empty and keeps
    later runs branching from the page the user already had. Idempotent: an
    environment that already has rows is skipped.
    """
    for row in conn.execute(
        "SELECT id, user_id, chat_id, files, updated_at FROM webpage_envs"
    ).fetchall():
        if conn.execute(
            "SELECT id FROM webpage_versions WHERE env_id = :env LIMIT 1",
            {"env": row["id"]},
        ).fetchone() is not None:
            continue
        files = _parse_webpage_files(row["files"])
        if not files:
            continue
        version_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO webpage_versions (id, env_id, user_id, chat_id, parent_version_id,"
            " seq, origin, message_uid, label, files, summary, created_at)"
            " VALUES (:id, :env_id, :user_id, :chat_id, NULL, 1, 'ai', NULL, 'Initial page',"
            " :files, :summary, :created_at)",
            {
                "id": version_id,
                "env_id": row["id"],
                "user_id": row["user_id"],
                "chat_id": row["chat_id"],
                "files": json.dumps(files),
                "summary": json.dumps(_webpage_diff_summary({}, files, [], [])),
                "created_at": row["updated_at"],
            },
        )
        conn.execute(
            "UPDATE webpage_envs SET active_version_id = :version WHERE id = :env",
            {"version": version_id, "env": row["id"]},
        )


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
    assistant reply is appended by append_chat_message().
    """
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
    images_dir().mkdir(parents=True, exist_ok=True)
    image_file_path(image_id, mime).write_bytes(data)
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
    attachments_dir().mkdir(parents=True, exist_ok=True)
    attachment_file_path(attachment_id, filename).write_bytes(data)
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


# ---------- webpage environments (the canvas) ----------

# Iterations kept per chat. The active iteration is never pruned; past the cap
# the oldest rows are deleted, so a pruned parent makes that child's diff fall
# back to "everything added" while its stored summary counts stay correct.
WEBPAGE_VERSION_LIMIT = 50

# Diff lines returned by one Changes-tab request, across all files.
WEBPAGE_DIFF_LINE_LIMIT = 2000

# Binary assets (a picture or file copied into the page's folder) live in the
# database, once per environment: iterations record only which asset paths they
# had, so a page that keeps the same picture across many runs stores it once,
# and deleting the environment takes the bytes with it.
WEBPAGE_MAX_ASSET_BYTES = 10 * 1024 * 1024
WEBPAGE_MAX_ASSET_TOTAL_BYTES = 20 * 1024 * 1024


def _parse_webpage_files(raw: str) -> dict[str, str]:
    try:
        data = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _parse_webpage_asset_paths(raw: str) -> list[str]:
    """One iteration's asset path list ([] when the column is unreadable)."""
    try:
        data = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _webpage_asset_map(user_id: str, chat_id: str) -> dict[str, dict]:
    """{path: {mime, size}} for the environment's assets — never the bytes.

    The canvas lists the folder from this and fetches whatever the page shows.
    """
    rows = get_conn().execute(
        "SELECT path, mime, size_bytes FROM webpage_assets"
        " WHERE chat_id = :chat_id AND user_id = :user_id ORDER BY path",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchall()
    return {row["path"]: {"mime": row["mime"], "size": row["size_bytes"]} for row in rows}


def _webpage_asset_paths_of_env(env_id: str) -> list[str]:
    rows = get_conn().execute(
        "SELECT path FROM webpage_assets WHERE env_id = :env_id ORDER BY path",
        {"env_id": env_id},
    ).fetchall()
    return [row["path"] for row in rows]


def _asset_line(path: str, info: dict) -> str:
    """How one asset reads in the Changes tab: "cat.png · image/png · 812 KB"."""
    parts = [path]
    if info.get("mime"):
        parts.append(str(info["mime"]))
    size = info.get("size")
    if isinstance(size, int) and size > 0:
        if size < 1024:
            parts.append(f"{size} B")
        elif size < 1024 * 1024:
            parts.append(f"{round(size / 1024)} KB")
        else:
            parts.append(f"{size / (1024 * 1024):.1f} MB")
    return " · ".join(parts)


def _webpage_env_result(
    env_id: str,
    user_id: str,
    chat_id: str,
    files: dict[str, str],
    updated_at: str,
    active_version_id: str | None,
) -> dict:
    """The environment shape every read/write path returns.

    Callers add their own extras on top ("version" for the commit paths,
    "asset" for a folder copy). `assets` is always the folder's metadata map,
    never the bytes.
    """
    return {
        "id": env_id,
        "user_id": user_id,
        "chat_id": chat_id,
        "files": files,
        "assets": _webpage_asset_map(user_id, chat_id),
        "updated_at": updated_at,
        "active_version_id": active_version_id,
    }


def get_webpage_env(user_id: str, chat_id: str) -> dict | None:
    """The chat's webpage environment as {id, files, assets, updated_at,
    active_version_id}.

    User-scoped: another user's chat (or a bogus id) is a miss. The files map
    is the active iteration's snapshot, and it is the server's source of truth —
    messages only carry a small {"type": "webpage", "webpageId", ...} part, so
    file text never flows through the full-history sync payloads. `assets` is
    the folder's binary side ({path: {mime, size}}), whose bytes only ever leave
    the server through the asset route.
    """
    row = get_conn().execute(
        "SELECT * FROM webpage_envs WHERE chat_id = :chat_id AND user_id = :user_id",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchone()
    if row is None:
        return None
    return _webpage_env_result(
        row["id"],
        row["user_id"],
        row["chat_id"],
        _parse_webpage_files(row["files"]),
        row["updated_at"],
        row["active_version_id"],
    )


def put_webpage_file(
    user_id: str,
    chat_id: str,
    path: str,
    content: str,
    *,
    message_uid: str | None = None,
    origin: str = "ai",
    label: str = "",
) -> dict:
    """Create or update one file, then commit the result as an iteration.

    The environment is created on the first write and reused after that, so the
    canvas always reflects the chat's current page. Every write also lands as a
    webpage_versions row (one per AI run via message_uid, its own row for a
    manual save). Returns the env as a dict plus "version" and
    "active_version_id".
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM webpage_envs WHERE chat_id = :chat_id AND user_id = :user_id",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchone()
    now = _now()
    if row is None:
        env_id = uuid.uuid4().hex
        files = {path: content}
        conn.execute(
            "INSERT INTO webpage_envs (id, user_id, chat_id, files, updated_at)"
            " VALUES (:id, :user_id, :chat_id, :files, :updated_at)",
            {
                "id": env_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "files": json.dumps(files),
                "updated_at": now,
            },
        )
    else:
        env_id = row["id"]
        files = _parse_webpage_files(row["files"])
        files[path] = content
        conn.execute(
            "UPDATE webpage_envs SET files = :files, updated_at = :updated_at"
            " WHERE id = :id",
            {"files": json.dumps(files), "updated_at": now, "id": env_id},
        )
    version = commit_webpage_version(
        user_id,
        chat_id,
        files,
        origin=origin,
        message_uid=message_uid,
        label=label,
    )
    result = _webpage_env_result(env_id, user_id, chat_id, files, now, version["id"])
    result["version"] = version
    return result


def _webpage_diff_summary(
    base: dict[str, str], files: dict[str, str], base_assets: list[str], assets: list[str]
) -> dict:
    """Changed-file count plus added/removed counts between two iterations.

    An asset (a picture or other binary in the folder) counts as one line of its
    own, so adding one reads as "+1" in the history like a line of code would.
    """
    added = 0
    removed = 0
    changed = 0
    for path in sorted(set(base) | set(files)):
        if path not in files:
            removed += len(base[path].splitlines())
            changed += 1
            continue
        if path not in base:
            added += len(files[path].splitlines())
            changed += 1
            continue
        opcodes = difflib.SequenceMatcher(
            None, base[path].splitlines(), files[path].splitlines(), autojunk=False
        ).get_opcodes()
        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "insert":
                added += j2 - j1
            elif tag == "delete":
                removed += i2 - i1
            elif tag == "replace":
                removed += i2 - i1
                added += j2 - j1
        if any(tag != "equal" for tag, *_ in opcodes):
            changed += 1
    changed += len(set(assets) ^ set(base_assets))
    added += len(set(assets) - set(base_assets))
    removed += len(set(base_assets) - set(assets))
    return {"files": changed, "added": added, "removed": removed}


def _webpage_version_row(row: sqlite3.Row) -> dict:
    """The list-item shape for one iteration (no file text)."""
    try:
        summary = json.loads(row["summary"]) if row["summary"] else {}
    except (ValueError, TypeError):
        summary = {}
    if not isinstance(summary, dict):
        summary = {}
    return {
        "id": row["id"],
        "seq": row["seq"],
        "parentVersionId": row["parent_version_id"],
        "origin": row["origin"],
        "label": row["label"],
        "messageUid": row["message_uid"],
        "assets": _parse_webpage_asset_paths(row["assets"]),
        "summary": summary,
        "createdAt": row["created_at"],
    }


def _webpage_version(chat_id: str, version_id: str | None) -> dict | None:
    """One iteration's files and asset paths, or None when it is gone (pruned)."""
    if not version_id:
        return None
    row = get_conn().execute(
        "SELECT files, assets FROM webpage_versions WHERE id = :id AND chat_id = :chat_id",
        {"id": version_id, "chat_id": chat_id},
    ).fetchone()
    if row is None:
        return None
    return {
        "files": _parse_webpage_files(row["files"]),
        "assets": _parse_webpage_asset_paths(row["assets"]),
    }


def commit_webpage_version(
    user_id: str,
    chat_id: str,
    files: dict[str, str],
    *,
    assets: list[str] | None = None,
    origin: str,
    message_uid: str | None = None,
    label: str = "",
) -> dict:
    """Record one iteration (the single write path) and make it active.

    With a message_uid, a later write in the same AI run refreshes that run's
    row — it keeps its id, seq, parent and created_at — so one reply is one
    iteration no matter how many files it writes. Without one, every call is a
    new iteration (manual canvas saves). Also writes the working copy, so
    webpage_envs.files always equals the active iteration's snapshot.

    ``assets`` is the folder's asset path list for this iteration; None means
    "the ones the environment already has" (an asset write is the only caller
    that changes it).
    """
    conn = get_conn()
    env = conn.execute(
        "SELECT * FROM webpage_envs WHERE chat_id = :chat_id AND user_id = :user_id",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchone()
    if env is None:
        raise ValueError("no webpage environment for this chat")
    if assets is None:
        assets = _webpage_asset_paths_of_env(env["id"])
    now = _now()
    hit = None
    if message_uid:
        hit = conn.execute(
            "SELECT * FROM webpage_versions WHERE chat_id = :chat_id AND message_uid = :uid",
            {"chat_id": chat_id, "uid": message_uid},
        ).fetchone()
    if hit is not None:
        version_id = hit["id"]
        seq = hit["seq"]
        parent = hit["parent_version_id"]
        created_at = hit["created_at"]
        base = _webpage_version(chat_id, parent) or {"files": {}, "assets": []}
        conn.execute(
            "UPDATE webpage_versions SET files = :files, assets = :assets, summary = :summary"
            " WHERE id = :id",
            {
                "files": json.dumps(files),
                "assets": json.dumps(assets),
                "summary": json.dumps(
                    _webpage_diff_summary(base["files"], files, base["assets"], assets)
                ),
                "id": version_id,
            },
        )
    else:
        version_id = uuid.uuid4().hex
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM webpage_versions WHERE chat_id = :chat_id",
            {"chat_id": chat_id},
        ).fetchone()["seq"]
        parent = env["active_version_id"]
        created_at = now
        base = _webpage_version(chat_id, parent) or {"files": {}, "assets": []}
        conn.execute(
            "INSERT INTO webpage_versions (id, env_id, user_id, chat_id, parent_version_id,"
            " seq, origin, message_uid, label, files, assets, summary, created_at)"
            " VALUES (:id, :env_id, :user_id, :chat_id, :parent, :seq, :origin, :message_uid,"
            " :label, :files, :assets, :summary, :created_at)",
            {
                "id": version_id,
                "env_id": env["id"],
                "user_id": user_id,
                "chat_id": chat_id,
                "parent": parent,
                "seq": seq,
                "origin": origin,
                "message_uid": message_uid,
                "label": label,
                "files": json.dumps(files),
                "assets": json.dumps(assets),
                "summary": json.dumps(
                    _webpage_diff_summary(base["files"], files, base["assets"], assets)
                ),
                "created_at": created_at,
            },
        )
    conn.execute(
        "UPDATE webpage_envs SET files = :files, updated_at = :now, active_version_id = :version"
        " WHERE id = :id",
        {"files": json.dumps(files), "now": now, "version": version_id, "id": env["id"]},
    )
    _prune_webpage_versions(conn, chat_id, version_id)
    conn.commit()
    row = conn.execute(
        "SELECT * FROM webpage_versions WHERE id = :id", {"id": version_id}
    ).fetchone()
    version = _webpage_version_row(row)
    version["files"] = files
    return version


def put_webpage_asset(
    user_id: str,
    chat_id: str,
    path: str,
    data: bytes,
    mime: str,
    *,
    origin: str = "ai",
    message_uid: str | None = None,
    label: str = "",
) -> dict:
    """Copy one picture or file into the page's folder, then commit an iteration.

    The bytes are stored once for the environment; iterations record the asset
    paths they had, so an older iteration that never showed the picture does not
    list it and no iteration stores a second copy. Returns the env dict plus
    "version" (the iteration that now holds the asset) and "asset".
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM webpage_envs WHERE chat_id = :chat_id AND user_id = :user_id",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchone()
    if row is None:
        raise ValueError("no webpage environment for this chat")
    now = _now()
    conn.execute(
        "INSERT INTO webpage_assets (id, env_id, user_id, chat_id, path, mime, size_bytes,"
        " data, created_at)"
        " VALUES (:id, :env_id, :user_id, :chat_id, :path, :mime, :size, :data, :created_at)"
        " ON CONFLICT(env_id, path) DO UPDATE SET mime = :mime, size_bytes = :size,"
        " data = :data, created_at = :created_at",
        {
            "id": uuid.uuid4().hex,
            "env_id": row["id"],
            "user_id": user_id,
            "chat_id": chat_id,
            "path": path,
            "mime": mime,
            "size": len(data),
            "data": data,
            "created_at": now,
        },
    )
    files = _parse_webpage_files(row["files"])
    assets = _webpage_asset_paths_of_env(row["id"])
    version = commit_webpage_version(
        user_id,
        chat_id,
        files,
        assets=assets,
        origin=origin,
        message_uid=message_uid,
        label=label,
    )
    result = _webpage_env_result(row["id"], user_id, chat_id, files, now, version["id"])
    result["version"] = version
    result["asset"] = {"path": path, "mime": mime, "size": len(data)}
    return result


def get_webpage_asset(user_id: str, chat_id: str, path: str) -> sqlite3.Row | None:
    """One asset row including its bytes; user- and chat-scoped.

    The route serving bytes is the only caller: everything else uses the
    metadata map from get_webpage_env.
    """
    return get_conn().execute(
        "SELECT * FROM webpage_assets"
        " WHERE chat_id = :chat_id AND user_id = :user_id AND path = :path",
        {"chat_id": chat_id, "user_id": user_id, "path": path},
    ).fetchone()


def _prune_webpage_versions(conn: sqlite3.Connection, chat_id: str, active_version_id: str) -> None:
    """Drop the oldest iterations past the cap, never the active one.

    DELETE ... LIMIT is not portable (it needs a compile flag SQLite may not
    have), so the doomed ids are selected first.
    """
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM webpage_versions WHERE chat_id = :chat_id",
        {"chat_id": chat_id},
    ).fetchone()["n"]
    excess = total - WEBPAGE_VERSION_LIMIT
    if excess <= 0:
        return
    conn.execute(
        "DELETE FROM webpage_versions WHERE id IN ("
        " SELECT id FROM webpage_versions WHERE chat_id = :chat AND id != :active"
        " ORDER BY seq ASC LIMIT :excess)",
        {"chat": chat_id, "active": active_version_id, "excess": excess},
    )


def list_webpage_versions(user_id: str, chat_id: str) -> list[dict]:
    """Every iteration of the chat, oldest first (no file text: the list stays small)."""
    rows = get_conn().execute(
        "SELECT * FROM webpage_versions WHERE chat_id = :chat_id AND user_id = :user_id"
        " ORDER BY seq ASC",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchall()
    return [_webpage_version_row(row) for row in rows]


def get_webpage_active_version(user_id: str, chat_id: str) -> str | None:
    """The id of the iteration later writes and manual edits apply to."""
    row = get_conn().execute(
        "SELECT active_version_id FROM webpage_envs WHERE chat_id = :chat_id AND user_id = :user_id",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchone()
    return row["active_version_id"] if row is not None else None


def get_webpage_version(user_id: str, chat_id: str, version_id: str) -> dict | None:
    """One iteration with its file snapshot, or None when it is not this user's."""
    row = get_conn().execute(
        "SELECT * FROM webpage_versions WHERE id = :id AND chat_id = :chat_id AND user_id = :user_id",
        {"id": version_id, "chat_id": chat_id, "user_id": user_id},
    ).fetchone()
    if row is None:
        return None
    version = _webpage_version_row(row)
    version["files"] = _parse_webpage_files(row["files"])
    return version


def set_webpage_active_version(user_id: str, chat_id: str, version_id: str) -> dict | None:
    """Activate one iteration: its snapshot becomes the working copy.

    None when the environment or the iteration is not this user's chat's. Later
    AI runs and manual saves branch from the activated iteration; the ones after
    it are kept.
    """
    row = get_webpage_version(user_id, chat_id, version_id)
    if row is None:
        return None
    conn = get_conn()
    env = conn.execute(
        "SELECT id FROM webpage_envs WHERE chat_id = :chat_id AND user_id = :user_id",
        {"chat_id": chat_id, "user_id": user_id},
    ).fetchone()
    if env is None:
        return None
    now = _now()
    files = row["files"]
    conn.execute(
        "UPDATE webpage_envs SET files = :files, active_version_id = :version, updated_at = :now"
        " WHERE id = :id",
        {"files": json.dumps(files), "version": version_id, "now": now, "id": env["id"]},
    )
    conn.commit()
    return _webpage_env_result(env["id"], user_id, chat_id, files, now, version_id)


def webpage_version_diff(
    user_id: str, chat_id: str, version_id: str, against: str | None = None
) -> dict | None:
    """Unified diff of one iteration against its parent (or an explicit base).

    None when the iteration is not this user's chat's. A pruned or missing base
    degrades to "everything added" with baseVersionId None, never an error.
    `against` lets the UI compare any two iterations.
    """
    row = get_webpage_version(user_id, chat_id, version_id)
    if row is None:
        return None
    base_id = against or row["parentVersionId"]
    base_row = _webpage_version(chat_id, base_id) if base_id else None
    if base_id and base_row is None:
        # The parent is gone (pruned) or the id is bogus: show the whole file.
        base_id = None
    base = base_row["files"] if base_row else {}
    base_assets = base_row["assets"] if base_row else []
    files_out: list[dict] = []
    budget = WEBPAGE_DIFF_LINE_LIMIT
    truncated = False
    for path in sorted(set(base) | set(row["files"])):
        if budget <= 0:
            truncated = True
            break
        in_base = path in base
        in_files = path in row["files"]
        if in_base and not in_files:
            status = "removed"
            base_lines: list[str] = base[path].splitlines()
            file_lines: list[str] = []
        elif in_files and not in_base:
            status = "added"
            base_lines = []
            file_lines = row["files"][path].splitlines()
        else:
            status = "modified"
            base_lines = base[path].splitlines()
            file_lines = row["files"][path].splitlines()
        added = 0
        removed = 0
        lines: list[dict] = []
        if status == "added":
            lines = [{"kind": "add", "text": text} for text in file_lines]
            added = len(file_lines)
        elif status == "removed":
            lines = [{"kind": "del", "text": text} for text in base_lines]
            removed = len(base_lines)
        else:
            matcher = difflib.SequenceMatcher(None, base_lines, file_lines, autojunk=False)
            for group in matcher.get_grouped_opcodes(3):
                head = group[0]
                tail = group[-1]
                lines.append(
                    {
                        "kind": "hunk",
                        "text": f"@@ -{head[1] + 1},{tail[2] - head[1]}"
                        f" +{head[3] + 1},{tail[4] - head[3]} @@",
                    }
                )
                for tag, i1, i2, j1, j2 in group:
                    if tag == "equal":
                        lines.extend(
                            {"kind": "ctx", "text": text} for text in base_lines[i1:i2]
                        )
                    else:
                        lines.extend(
                            {"kind": "del", "text": text} for text in base_lines[i1:i2]
                        )
                        lines.extend(
                            {"kind": "add", "text": text} for text in file_lines[j1:j2]
                        )
                        removed += i2 - i1
                        added += j2 - j1
        if not lines:
            continue
        if len(lines) > budget:
            lines = lines[:budget]
            truncated = True
        budget -= len(lines)
        files_out.append(
            {
                "path": path,
                "status": status,
                "added": added,
                "removed": removed,
                "lines": lines,
            }
        )
    # A picture (or other binary) the folder gained or lost: one line describing
    # it, so the Changes tab accounts for every path the run touched.
    now_assets = set(row["assets"])
    was_assets = set(base_assets)
    asset_info = _webpage_asset_map(user_id, chat_id) if now_assets ^ was_assets else {}
    for path in sorted(now_assets ^ was_assets):
        if budget <= 0:
            truncated = True
            break
        added_now = path in now_assets
        budget -= 1
        files_out.append(
            {
                "path": path,
                "status": "added" if added_now else "removed",
                "added": 1 if added_now else 0,
                "removed": 0 if added_now else 1,
                "lines": [
                    {
                        "kind": "add" if added_now else "del",
                        "text": _asset_line(path, asset_info.get(path) or {}),
                    }
                ],
            }
        )
    return {
        "versionId": version_id,
        "baseVersionId": base_id,
        "files": files_out,
        "truncated": truncated,
    }
