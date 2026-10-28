"""PostgreSQL storage for accounts, sessions and chat history.

Connection settings come from DATABASE_URL (see app/config.py). Chat storage is
server-side only: the client mirrors what is stored here. Images, attachments
and website workspaces stay on disk under DATA_DIR.

Every function here is synchronous and borrows a connection from a small pool
for the length of one call (or one transaction), so the event loop and the
worker threads that run sync tools can use it at the same time. Rows come back
as plain dicts, with timestamps as ISO-8601 strings (UTC) and JSONB columns as
Python lists/dicts.
"""

import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app import config

DATABASE_URL = config.DATABASE_URL
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

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


# A session ends after SESSION_TTL without use, and SESSION_MAX_AGE after
# sign-in however active it stays. Use renews it only once less than
# SESSION_RENEW_WINDOW is left, so most requests do not write.
SESSION_TTL = timedelta(days=30)
SESSION_MAX_AGE = timedelta(days=90)
SESSION_RENEW_WINDOW = SESSION_TTL / 2

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10
# Arbitrary constant naming the advisory lock that serializes schema upgrades
# between processes starting at the same moment.
_MIGRATION_LOCK_KEY = 7_204_911_001


def _row_factory(cursor):
    """dict rows whose timestamps are ISO-8601 UTC strings.

    The rest of the app was written against text timestamps (they are stored,
    compared and parsed with fromisoformat), so the conversion happens once,
    here, at the boundary. Booleans and JSONB already arrive as Python values.
    """
    make = dict_row(cursor)

    def build(values):
        row = make(values)
        for key, value in row.items():
            if isinstance(value, datetime):
                row[key] = value.astimezone(timezone.utc).isoformat()
        return row

    return build


_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> ConnectionPool:
    """The shared pool, opened (and the schema upgraded) on first use."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                pool = ConnectionPool(
                    DATABASE_URL,
                    min_size=POOL_MIN_SIZE,
                    max_size=POOL_MAX_SIZE,
                    kwargs={"row_factory": _row_factory, "options": "-c timezone=UTC"},
                    check=ConnectionPool.check_connection,
                    open=False,
                )
                try:
                    pool.open(wait=True, timeout=15)
                    _apply_migrations(pool)
                except BaseException:
                    pool.close()
                    raise
                _pool = pool
    return _pool


def close_pool() -> None:
    """Close every connection; the next call to get_pool() opens a fresh pool."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.close()


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """A pooled connection in one transaction: commit on success, roll back on error."""
    with get_pool().connection() as conn:
        yield conn


def _fetchone(sql: str, params: dict | None = None) -> dict | None:
    with connection() as conn:
        return conn.execute(sql, params).fetchone()


def _fetchall(sql: str, params: dict | None = None) -> list[dict]:
    with connection() as conn:
        return conn.execute(sql, params).fetchall()


def _execute(sql: str, params: dict | None = None) -> int:
    """Run one statement in its own transaction and return its rowcount."""
    with connection() as conn:
        return conn.execute(sql, params).rowcount


# ---------- schema migrations ----------

def _load_migrations() -> list[tuple[int, str, str]]:
    """(version, name, sql) from migrations/NNNN_name.sql, in version order."""
    found = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        version, _, name = path.stem.partition("_")
        found.append((int(version), name, path.read_text(encoding="utf-8")))
    return sorted(found)


def _apply_migrations(pool: ConnectionPool) -> None:
    """Apply every migration not yet recorded in schema_migrations.

    Runs in one transaction under an advisory lock, so two processes starting
    together queue up instead of both creating tables, and a failed migration
    leaves nothing half-applied. To change the schema, add the next numbered
    file to app/migrations/; never edit one that has shipped.
    """
    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for version, _name, sql in _load_migrations():
            if version in applied:
                continue
            conn.execute(sql)  # no parameters, so several statements are fine
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: str | None) -> str | None:
    """PostgreSQL text cannot hold NUL; drop it rather than fail the write."""
    return value.replace("\x00", "") if isinstance(value, str) else value


def _json(value: Any) -> Jsonb:
    """A JSONB parameter, with NULs stripped from every string (jsonb rejects them)."""

    def clean(item: Any) -> Any:
        if isinstance(item, str):
            return _text(item)
        if isinstance(item, list):
            return [clean(i) for i in item]
        if isinstance(item, dict):
            return {_text(k): clean(v) for k, v in item.items()}
        return item

    return Jsonb(clean(value))


# ---------- users ----------

def create_google_user(
    email: str, display_name: str, google_sub: str, avatar_url: str | None = None
) -> dict:
    row = {
        "id": uuid.uuid4().hex,
        "email": email,
        "display_name": display_name,
        "google_sub": google_sub,
        "avatar_url": avatar_url,
        "created_at": _now(),
    }
    _execute(
        "INSERT INTO users (id, email, display_name, google_sub, avatar_url, created_at)"
        " VALUES (%(id)s, %(email)s, %(display_name)s, %(google_sub)s, %(avatar_url)s,"
        " %(created_at)s)",
        row,
    )
    created = get_user_by_id(row["id"])
    assert created is not None
    return created


def get_user_by_email(email: str) -> dict | None:
    return _fetchone("SELECT * FROM users WHERE email = %(email)s", {"email": email})


def get_user_by_google_sub(google_sub: str) -> dict | None:
    return _fetchone("SELECT * FROM users WHERE google_sub = %(sub)s", {"sub": google_sub})


def get_user_by_id(user_id: str) -> dict | None:
    return _fetchone("SELECT * FROM users WHERE id = %(id)s", {"id": user_id})


def link_google_account(
    user_id: str, google_sub: str, avatar_url: str | None = None
) -> None:
    _execute(
        "UPDATE users SET google_sub = %(sub)s, avatar_url = %(avatar)s WHERE id = %(id)s",
        {"sub": google_sub, "avatar": avatar_url, "id": user_id},
    )


def update_user_display_name(user_id: str, display_name: str) -> None:
    _execute(
        "UPDATE users SET display_name = %(name)s WHERE id = %(id)s",
        {"name": display_name, "id": user_id},
    )


def update_user_adult_mode(user_id: str, enabled: bool) -> None:
    _execute(
        "UPDATE users SET adult_mode = %(v)s WHERE id = %(id)s",
        {"v": bool(enabled), "id": user_id},
    )


def set_memory_enabled(user_id: str, enabled: bool) -> None:
    _execute(
        "UPDATE users SET memory_enabled = %(v)s WHERE id = %(id)s",
        {"v": bool(enabled), "id": user_id},
    )


# ---------- memories ----------

def list_memories(user_id: str, limit: int | None = None) -> list[dict]:
    """The user's memories, most recently touched first."""
    sql = "SELECT * FROM memories WHERE user_id = %(uid)s ORDER BY updated_at DESC, id"
    params: dict[str, Any] = {"uid": user_id}
    if limit is not None:
        sql += " LIMIT %(limit)s"
        params["limit"] = limit
    return _fetchall(sql, params)


def get_memory(user_id: str, memory_id: str) -> dict | None:
    return _fetchone(
        "SELECT * FROM memories WHERE id = %(id)s AND user_id = %(uid)s",
        {"id": memory_id, "uid": user_id},
    )


def count_memories(user_id: str) -> int:
    row = _fetchone(
        "SELECT COUNT(*) AS n FROM memories WHERE user_id = %(uid)s", {"uid": user_id}
    )
    assert row is not None
    return int(row["n"])


def create_memory(
    user_id: str, content: str, source_chat_id: str | None = None
) -> dict:
    """Insert a memory; an identical one (ignoring case) is touched and returned instead."""
    content = _text(content) or ""
    with connection() as conn:
        # Serialize concurrent saves for one user so the duplicate check and
        # the insert cannot interleave; the lock is released at commit.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%(key)s))", {"key": f"memory:{user_id}"}
        )
        existing = conn.execute(
            "SELECT id FROM memories WHERE user_id = %(uid)s AND lower(content) = lower(%(c)s)",
            {"uid": user_id, "c": content},
        ).fetchone()
        if existing is not None:
            memory_id = existing["id"]
            conn.execute(
                "UPDATE memories SET updated_at = %(now)s WHERE id = %(id)s",
                {"now": _now(), "id": memory_id},
            )
        else:
            now = _now()
            memory_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO memories (id, user_id, content, source_chat_id, created_at,"
                " updated_at) VALUES (%(id)s, %(uid)s, %(c)s, %(chat)s, %(now)s, %(now)s)",
                {
                    "id": memory_id,
                    "uid": user_id,
                    "c": content,
                    "chat": source_chat_id or None,
                    "now": now,
                },
            )
        saved = conn.execute(
            "SELECT * FROM memories WHERE id = %(id)s AND user_id = %(uid)s",
            {"id": memory_id, "uid": user_id},
        ).fetchone()
    assert saved is not None
    return saved


def update_memory(user_id: str, memory_id: str, content: str) -> dict | None:
    with connection() as conn:
        return conn.execute(
            "UPDATE memories SET content = %(c)s, updated_at = %(now)s"
            " WHERE id = %(id)s AND user_id = %(uid)s RETURNING *",
            {"c": _text(content), "now": _now(), "id": memory_id, "uid": user_id},
        ).fetchone()


def delete_memory(user_id: str, memory_id: str) -> bool:
    return bool(
        _execute(
            "DELETE FROM memories WHERE id = %(id)s AND user_id = %(uid)s",
            {"id": memory_id, "uid": user_id},
        )
    )


def delete_all_memories(user_id: str) -> int:
    return _execute("DELETE FROM memories WHERE user_id = %(uid)s", {"uid": user_id})


# ---------- Google grants (per-service OAuth tokens) ----------

def save_google_grant(
    user_id: str,
    service: str,
    scopes: str,
    access_token: str,
    refresh_token: str,
    expires_at: str | None,
) -> None:
    """Store a service's tokens. An empty refresh_token keeps the stored one."""
    now = _now()
    _execute(
        """
        INSERT INTO google_grants
            (user_id, service, scopes, access_token, refresh_token, expires_at, granted_at, updated_at)
        VALUES
            (%(user_id)s, %(service)s, %(scopes)s, %(access_token)s, %(refresh_token)s,
             %(expires_at)s::timestamptz, %(now)s, %(now)s)
        ON CONFLICT (user_id, service) DO UPDATE SET
            scopes = EXCLUDED.scopes,
            access_token = EXCLUDED.access_token,
            refresh_token = CASE
                WHEN EXCLUDED.refresh_token = '' THEN google_grants.refresh_token
                ELSE EXCLUDED.refresh_token
            END,
            expires_at = EXCLUDED.expires_at,
            updated_at = EXCLUDED.updated_at
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


def get_google_grant(user_id: str, service: str) -> dict | None:
    return _fetchone(
        "SELECT * FROM google_grants WHERE user_id = %(user_id)s AND service = %(service)s",
        {"user_id": user_id, "service": service},
    )


def list_google_grants(user_id: str) -> list[dict]:
    return _fetchall(
        "SELECT * FROM google_grants WHERE user_id = %(user_id)s",
        {"user_id": user_id},
    )


def list_google_grants_for_services(services: list[str]) -> list[dict]:
    """Every user's grants for these services (the switched-off integrations)."""
    return _fetchall(
        "SELECT * FROM google_grants WHERE service = ANY(%(services)s)",
        {"services": services},
    )


def delete_google_grant(user_id: str, service: str) -> None:
    _execute(
        "DELETE FROM google_grants WHERE user_id = %(user_id)s AND service = %(service)s",
        {"user_id": user_id, "service": service},
    )


# ---------- sent Gmail drafts ----------

def mark_draft_sent(user_id: str, draft_id: str, message_id: str = "") -> None:
    """Record that a draft was actually sent so the UI can stop offering Send."""
    _execute(
        "INSERT INTO sent_drafts (user_id, draft_id, message_id, sent_at)"
        " VALUES (%(user_id)s, %(draft_id)s, %(message_id)s, %(sent_at)s)"
        " ON CONFLICT (user_id, draft_id) DO UPDATE SET"
        " message_id = EXCLUDED.message_id, sent_at = EXCLUDED.sent_at",
        {
            "user_id": user_id,
            "draft_id": draft_id,
            "message_id": message_id,
            "sent_at": _now(),
        },
    )


def get_sent_draft_ids(user_id: str) -> set[str]:
    rows = _fetchall(
        "SELECT draft_id FROM sent_drafts WHERE user_id = %(user_id)s",
        {"user_id": user_id},
    )
    return {row["draft_id"] for row in rows}


# ---------- user-confirmed calendar deletions ----------

def mark_event_deleted(user_id: str, event_id: str) -> None:
    """Record that a proposed deletion was confirmed, so the card stays done
    after a reload (mirrors sent_drafts for email)."""
    _execute(
        "INSERT INTO deleted_events (user_id, event_id, deleted_at)"
        " VALUES (%(user_id)s, %(event_id)s, %(deleted_at)s)"
        " ON CONFLICT (user_id, event_id) DO UPDATE SET deleted_at = EXCLUDED.deleted_at",
        {"user_id": user_id, "event_id": event_id, "deleted_at": _now()},
    )


def get_deleted_event_ids(user_id: str) -> set[str]:
    rows = _fetchall(
        "SELECT event_id FROM deleted_events WHERE user_id = %(user_id)s",
        {"user_id": user_id},
    )
    return {row["event_id"] for row in rows}


# ---------- OAuth state (CSRF protection) ----------

def create_oauth_state(purpose: str, user_id: str | None = None) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex
    with connection() as conn:
        conn.execute("DELETE FROM oauth_states WHERE expires_at < now()")
        conn.execute(
            "INSERT INTO oauth_states (token, purpose, user_id, created_at, expires_at)"
            " VALUES (%(token)s, %(purpose)s, %(user_id)s, %(created_at)s, %(expires_at)s)",
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
    return token


def consume_oauth_state(token: str, purpose: str) -> dict | None:
    """Fetch and delete a state token in one shot; returns None if invalid.

    The DELETE ... RETURNING is atomic, so two callbacks racing on one token
    cannot both succeed. A token with the wrong purpose or past its expiry is
    still removed, but reported as invalid.
    """
    if not token:
        return None
    row = _fetchone(
        "DELETE FROM oauth_states WHERE token = %(token)s RETURNING *,"
        " (purpose = %(purpose)s AND expires_at > now()) AS valid",
        {"token": token, "purpose": purpose},
    )
    if row is None or not row.pop("valid"):
        return None
    return row


# ---------- sessions ----------

def create_session(user_id: str) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex
    _execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at)"
        " VALUES (%(token)s, %(user_id)s, %(created_at)s, %(expires_at)s)",
        {
            "token": token,
            "user_id": user_id,
            "created_at": _now(),
            "expires_at": (datetime.now(timezone.utc) + SESSION_TTL).isoformat(),
        },
    )
    return token


def get_user_for_session(token: str) -> dict | None:
    if not token:
        return None
    row = _fetchone(
        "SELECT u.*, s.expires_at < now() + %(renew_window)s AS renew"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token = %(token)s AND s.expires_at > now()",
        {"token": token, "renew_window": SESSION_RENEW_WINDOW},
    )
    if row is None:
        return None
    if row.pop("renew"):
        _execute(
            "UPDATE sessions SET expires_at = LEAST(now() + %(ttl)s, created_at + %(max_age)s)"
            " WHERE token = %(token)s",
            {"token": token, "ttl": SESSION_TTL, "max_age": SESSION_MAX_AGE},
        )
    return row


def delete_session(token: str) -> None:
    _execute("DELETE FROM sessions WHERE token = %(token)s", {"token": token})


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
    _execute(
        "INSERT INTO chats (id, user_id, title, mode, subject, created_at, updated_at)"
        " VALUES (%(id)s, %(user_id)s, %(title)s, %(mode)s, %(subject)s, %(created_at)s,"
        " %(updated_at)s)",
        row,
    )
    return {k: row[k] for k in ("id", "title", "mode", "subject")}


def get_chat(user_id: str, chat_id: str) -> dict | None:
    return _fetchone(
        "SELECT * FROM chats WHERE id = %(id)s AND user_id = %(user_id)s",
        {"id": chat_id, "user_id": user_id},
    )


def list_chats(user_id: str) -> list[dict]:
    chats = _fetchall(
        "SELECT id, title, mode, subject,"
        ' active_leaf_uid AS "activeLeafId" FROM chats'
        " WHERE user_id = %(user_id)s ORDER BY created_at DESC",
        {"user_id": user_id},
    )
    messages_grouped = list_messages_by_chat([c["id"] for c in chats])
    for chat in chats:
        chat["messages"] = messages_grouped.get(chat["id"], [])
    return chats


def list_messages_by_chat(chat_ids: list[str]) -> dict[str, list[dict]]:
    if not chat_ids:
        return {}
    rows = _fetchall(
        "SELECT chat_id, uid, parent_uid, branch_index, role, content, reasoning, tools,"
        " parts, thinking_done, thought_seconds FROM messages"
        " WHERE chat_id = ANY(%(ids)s) ORDER BY id",
        {"ids": list(chat_ids)},
    )
    grouped: dict[str, list[dict]] = {cid: [] for cid in chat_ids}
    for r in rows:
        grouped[r["chat_id"]].append(
            {
                "id": r["uid"],
                "parentId": r["parent_uid"],
                "branchIndex": r["branch_index"] or 0,
                "role": r["role"],
                "content": r["content"],
                "reasoning": r["reasoning"],
                "tools": r["tools"] or [],
                "parts": r["parts"] or [],
                "thinkingDone": bool(r["thinking_done"]),
                "thoughtSeconds": r["thought_seconds"],
            }
        )
    return grouped


def rename_chat(user_id: str, chat_id: str, title: str) -> bool:
    return _execute(
        "UPDATE chats SET title = %(title)s, updated_at = %(now)s"
        " WHERE id = %(id)s AND user_id = %(user_id)s",
        {"title": title, "now": _now(), "id": chat_id, "user_id": user_id},
    ) > 0


def set_chat_active_leaf(user_id: str, chat_id: str, leaf_uid: str | None) -> bool:
    """Remember which branch tail the user is viewing, so it survives reloads
    and follows them across devices."""
    return _execute(
        "UPDATE chats SET active_leaf_uid = %(leaf)s, updated_at = %(now)s"
        " WHERE id = %(id)s AND user_id = %(user_id)s",
        {"leaf": leaf_uid, "now": _now(), "id": chat_id, "user_id": user_id},
    ) > 0


def _doomed_files(conn: psycopg.Connection, where: str, params: dict) -> tuple[list, list, list]:
    """Image, attachment and webpage-site rows matching `where`, read before a delete."""
    return (
        conn.execute(f"SELECT id, mime FROM images WHERE {where}", params).fetchall(),
        conn.execute(f"SELECT id, filename FROM attachments WHERE {where}", params).fetchall(),
        conn.execute(f"SELECT id FROM webpage_sites WHERE {where}", params).fetchall(),
    )


def _remove_files(images: list, attachments: list, sites: list) -> None:
    """Delete the on-disk side of rows that ON DELETE CASCADE already dropped.

    The rows go with their chat or user; the files, site folders and site
    containers need explicit cleanup so a deletion leaves no orphan bytes.
    """
    for row in images:
        try:
            image_file_path(row["id"], row["mime"]).unlink(missing_ok=True)
        except OSError:
            pass
    for row in attachments:
        try:
            attachment_file_path(row["id"], row["filename"]).unlink(missing_ok=True)
        except OSError:
            pass
    if sites:
        # Imported here: sitebox imports this module, so a module-level import
        # would be circular.
        from app import sitebox

        for row in sites:
            sitebox.destroy_site(row["id"])


def delete_chat(user_id: str, chat_id: str) -> bool:
    """Delete one chat with its messages, files, images and webpage.

    Removing the webpage's container can take a while, so async callers run
    this in a worker thread.
    """
    with connection() as conn:
        doomed = _doomed_files(
            conn,
            "chat_id = %(chat_id)s AND user_id = %(user_id)s",
            {"chat_id": chat_id, "user_id": user_id},
        )
        deleted = conn.execute(
            "DELETE FROM chats WHERE id = %(id)s AND user_id = %(user_id)s",
            {"id": chat_id, "user_id": user_id},
        ).rowcount
    if deleted > 0:
        _remove_files(*doomed)
    return deleted > 0


def delete_user(user_id: str) -> bool:
    """Delete an account and everything linked to it.

    ON DELETE CASCADE drops the sessions, chats (and their messages), memories,
    Google grants, sent-draft and deleted-event markers, and the image,
    attachment and webpage-site rows. oauth_states has no foreign key, so its
    rows go explicitly. Revoking the Google tokens with Google is the caller's
    job (google_oauth.revoke_grants), since it needs the tokens this deletes.
    """
    with connection() as conn:
        doomed = _doomed_files(conn, "user_id = %(user_id)s", {"user_id": user_id})
        conn.execute("DELETE FROM oauth_states WHERE user_id = %(user_id)s", {"user_id": user_id})
        deleted = conn.execute(
            "DELETE FROM users WHERE id = %(user_id)s", {"user_id": user_id}
        ).rowcount
    if deleted > 0:
        _remove_files(*doomed)
    return deleted > 0


_INSERT_MESSAGE = (
    "INSERT INTO messages (chat_id, uid, parent_uid, branch_index, role, content,"
    " reasoning, tools, parts, thinking_done, thought_seconds, created_at)"
    " VALUES (%(chat_id)s, %(uid)s, %(parent_uid)s, %(branch_index)s, %(role)s, %(content)s,"
    " %(reasoning)s, %(tools)s, %(parts)s, %(thinking_done)s, %(thought_seconds)s,"
    " %(created_at)s)"
)


# Re-syncing an unchanged node is a no-op: the upsert only rewrites a row when
# one of its columns actually differs, so a turn writes just the new nodes (and
# any the client edited) instead of the whole history.
_UPSERT_MESSAGE = (
    _INSERT_MESSAGE
    + " ON CONFLICT (chat_id, uid) DO UPDATE SET"
    " parent_uid = EXCLUDED.parent_uid, branch_index = EXCLUDED.branch_index,"
    " role = EXCLUDED.role, content = EXCLUDED.content, reasoning = EXCLUDED.reasoning,"
    " tools = EXCLUDED.tools, parts = EXCLUDED.parts,"
    " thinking_done = EXCLUDED.thinking_done, thought_seconds = EXCLUDED.thought_seconds"
    " WHERE (messages.parent_uid, messages.branch_index, messages.role, messages.content,"
    " messages.reasoning, messages.tools, messages.parts, messages.thinking_done,"
    " messages.thought_seconds)"
    " IS DISTINCT FROM (EXCLUDED.parent_uid, EXCLUDED.branch_index, EXCLUDED.role,"
    " EXCLUDED.content, EXCLUDED.reasoning, EXCLUDED.tools, EXCLUDED.parts,"
    " EXCLUDED.thinking_done, EXCLUDED.thought_seconds)"
)


def replace_chat_messages(chat_id: str, messages: list[dict]) -> None:
    """Full-tree sync: the stored messages become the given tree.

    The client is treated as authoritative for message structure; each node
    carries its uid, parent link and sibling order. Nodes the tree no longer
    has are deleted, new ones inserted, and changed ones updated in place, so
    unchanged history (and its created_at) is left alone. The assistant reply
    is appended by append_chat_message(). The sync commits as one transaction,
    so a concurrent read (the sidebar refreshing, another device syncing) can
    never catch the tree half-replaced.
    """
    uids = [m.get("uid") for m in messages]
    with connection() as conn:
        conn.execute(
            "DELETE FROM messages WHERE chat_id = %(chat_id)s"
            " AND (uid IS NULL OR NOT uid = ANY(%(uids)s))",
            {"chat_id": chat_id, "uids": uids},
        )
        with conn.cursor() as cur:
            cur.executemany(
                _UPSERT_MESSAGE,
                [
                    {
                        "chat_id": chat_id,
                        "uid": m.get("uid"),
                        "parent_uid": m.get("parent_uid"),
                        "branch_index": m.get("branch_index", 0) or 0,
                        "role": m["role"],
                        "content": _text(m["content"]),
                        "reasoning": _text(m.get("reasoning", "")),
                        "tools": _json(m.get("tools", [])),
                        "parts": _json(m.get("parts", [])),
                        "thinking_done": bool(m.get("thinking_done", False)),
                        "thought_seconds": m.get("thought_seconds"),
                        "created_at": _now(),
                    }
                    for m in messages
                ],
            )
        conn.execute(
            "UPDATE chats SET updated_at = %(now)s WHERE id = %(chat_id)s",
            {"now": _now(), "chat_id": chat_id},
        )


def update_chat_meta(chat_id: str, mode: str | None = None,
                     subject: str | None = None,
                     clear_subject: bool = False) -> None:
    sets = ["updated_at = %(now)s"]
    params: dict = {"now": _now(), "chat_id": chat_id}
    if mode is not None:
        sets.append("mode = %(mode)s")
        params["mode"] = mode
    if clear_subject:
        sets.append("subject = NULL")
    elif subject is not None:
        sets.append("subject = %(subject)s")
        params["subject"] = subject
    _execute(f"UPDATE chats SET {', '.join(sets)} WHERE id = %(chat_id)s", params)


def append_chat_message(chat_id: str, role: str, content: str,
                        reasoning: str = "", tools: list | None = None,
                        parts: list | None = None,
                        thinking_done: bool = False,
                        thought_seconds: float | None = None,
                        uid: str | None = None,
                        parent_uid: str | None = None) -> None:
    """Append a message to the tree, deriving its sibling order when parented.

    ``branch_index`` follows the same max+1 rule the client uses, so the two
    stay in lockstep without the client having to precompute it. The chat row
    is locked first so concurrent appends can't read the same max and hand two
    siblings one index; the lock releases when the transaction commits.
    """
    with connection() as conn:
        if parent_uid is None:
            branch_index = 0
        else:
            conn.execute(
                "SELECT 1 FROM chats WHERE id = %(chat_id)s FOR UPDATE",
                {"chat_id": chat_id},
            )
            row = conn.execute(
                "SELECT MAX(branch_index) AS mx FROM messages"
                " WHERE chat_id = %(chat_id)s AND parent_uid = %(parent_uid)s",
                {"chat_id": chat_id, "parent_uid": parent_uid},
            ).fetchone()
            branch_index = (row["mx"] + 1) if row and row["mx"] is not None else 0
        conn.execute(
            _INSERT_MESSAGE,
            {
                "chat_id": chat_id,
                "uid": uid,
                "parent_uid": parent_uid,
                "branch_index": branch_index,
                "role": role,
                "content": _text(content),
                "reasoning": _text(reasoning),
                "tools": _json(tools or []),
                "parts": _json(parts or []),
                "thinking_done": bool(thinking_done),
                "thought_seconds": thought_seconds,
                "created_at": _now(),
            },
        )
        conn.execute(
            "UPDATE chats SET updated_at = %(now)s WHERE id = %(chat_id)s",
            {"now": _now(), "chat_id": chat_id},
        )


def set_chat_title_if_default(chat_id: str, title: str) -> None:
    _execute(
        "UPDATE chats SET title = %(title)s, updated_at = %(now)s"
        " WHERE id = %(chat_id)s AND title = 'New chat'",
        {"title": title, "now": _now(), "chat_id": chat_id},
    )


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
    _execute(
        "INSERT INTO images (id, user_id, chat_id, mime, size_bytes, width,"
        " height, prompt, created_at)"
        " VALUES (%(id)s, %(user_id)s, %(chat_id)s, %(mime)s, %(size_bytes)s, %(width)s,"
        " %(height)s, %(prompt)s, %(created_at)s)",
        {
            "id": image_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "mime": mime,
            "size_bytes": len(data),
            "width": width,
            "height": height,
            "prompt": _text(prompt),
            "created_at": _now(),
        },
    )
    created = get_image(user_id, image_id)
    assert created is not None
    return created


def get_image(user_id: str, image_id: str) -> dict | None:
    """User-scoped lookup: another user's image (or a bogus id) is a miss."""
    return _fetchone(
        "SELECT * FROM images WHERE id = %(id)s AND user_id = %(user_id)s",
        {"id": image_id, "user_id": user_id},
    )


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
    text = _text(text) or ""
    _execute(
        "INSERT INTO attachments (id, user_id, chat_id, filename, mime, kind,"
        " size_bytes, text, char_count, created_at)"
        " VALUES (%(id)s, %(user_id)s, %(chat_id)s, %(filename)s, %(mime)s, %(kind)s,"
        " %(size_bytes)s, %(text)s, %(char_count)s, %(created_at)s)",
        {
            "id": attachment_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "filename": _text(filename),
            "mime": mime,
            "kind": kind,
            "size_bytes": len(data),
            "text": text,
            "char_count": len(text),
            "created_at": _now(),
        },
    )
    created = get_attachment(user_id, attachment_id)
    assert created is not None
    return created


def get_attachment(user_id: str, attachment_id: str) -> dict | None:
    """User-scoped lookup: another user's file (or a bogus id) is a miss."""
    return _fetchone(
        "SELECT * FROM attachments WHERE id = %(id)s AND user_id = %(user_id)s",
        {"id": attachment_id, "user_id": user_id},
    )


def delete_attachment(user_id: str, attachment_id: str) -> bool:
    row = _fetchone(
        "DELETE FROM attachments WHERE id = %(id)s AND user_id = %(user_id)s"
        " RETURNING id, filename",
        {"id": attachment_id, "user_id": user_id},
    )
    if row is None:
        return False
    try:
        attachment_file_path(row["id"], row["filename"]).unlink(missing_ok=True)
    except OSError:
        pass
    return True


# ---------- webpage sites (the canvas) ----------

# The site row is only a registry: the page itself is a folder under
# backend/data/sites/{id} with a git history, and the container runtime in
# app/sitebox.py owns both. Keeping just the pointer here means the database
# never holds file bytes, so file size and history length are disk concerns.


def get_webpage_site(user_id: str, chat_id: str) -> dict | None:
    """The chat's website workspace, or None when the chat has none yet.

    User-scoped: another user's chat (or a bogus id) is a miss. `preview_key`
    is the capability token in the preview URL, and `active_commit` is the git
    commit the canvas currently serves (None before the first commit).
    """
    return _fetchone(
        "SELECT * FROM webpage_sites WHERE chat_id = %(chat_id)s AND user_id = %(user_id)s",
        {"chat_id": chat_id, "user_id": user_id},
    )


def get_webpage_site_by_chat(chat_id: str) -> dict | None:
    """The chat's website workspace without a user scope, or None.

    Only the capability-URL preview route uses this: that route has no session
    (module scripts inside the sandboxed iframe send cookies at best, and the
    iframe has an opaque origin), so the unguessable `preview_key` in its path
    is the authorization. Every user-facing route stays scoped by user_id.
    """
    return _fetchone(
        "SELECT * FROM webpage_sites WHERE chat_id = %(chat_id)s", {"chat_id": chat_id}
    )


_INSERT_SITE = (
    "INSERT INTO webpage_sites (id, user_id, chat_id, preview_key, active_commit,"
    " created_at, updated_at)"
    " VALUES (%(id)s, %(user_id)s, %(chat_id)s, %(preview_key)s, NULL, %(created_at)s,"
    " %(updated_at)s)"
)


def _new_site_params(user_id: str, chat_id: str, preview_key: str) -> dict:
    now = _now()
    return {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "chat_id": chat_id,
        "preview_key": preview_key,
        "created_at": now,
        "updated_at": now,
    }


def create_webpage_site(user_id: str, chat_id: str, preview_key: str) -> dict:
    """Register the chat's workspace and return the row as a dict.

    The workspace folder and its git repo are created by the container layer;
    this only claims the row, so a crash between the two leaves an empty
    workspace rather than an unreachable page. `chat_id` is UNIQUE, so the
    caller checks get_webpage_site first (one workspace per chat).
    """
    _execute(_INSERT_SITE, _new_site_params(user_id, chat_id, preview_key))
    created = get_webpage_site(user_id, chat_id)
    assert created is not None
    return created


def get_or_create_webpage_site(user_id: str, chat_id: str, preview_key: str) -> dict:
    """The chat's site, created with ``preview_key`` on first use.

    Several tool calls of a single model message execute in parallel (the tool
    node gathers them) and every one of them ensures the site before its first
    write, so this must be race-free: the insert is ON CONFLICT DO NOTHING on
    the unique chat_id, and every caller then reads the one winning row.
    """
    _execute(
        _INSERT_SITE + " ON CONFLICT (chat_id) DO NOTHING",
        _new_site_params(user_id, chat_id, preview_key),
    )
    site = get_webpage_site(user_id, chat_id)
    if site is None:
        raise LookupError(f"chat {chat_id} already has a site that belongs to another user")
    return site


def set_webpage_active_commit(user_id: str, chat_id: str, commit: str) -> None:
    """Point the canvas at one commit's snapshot (a checkout).

    Also bumps updated_at: switching what the preview serves is a change to the
    site, and touch_webpage_site's idle accounting should see it.
    """
    _execute(
        "UPDATE webpage_sites SET active_commit = %(commit)s, updated_at = %(now)s"
        " WHERE chat_id = %(chat_id)s AND user_id = %(user_id)s",
        {"commit": commit, "now": _now(), "chat_id": chat_id, "user_id": user_id},
    )


def touch_webpage_site(user_id: str, chat_id: str) -> None:
    """Bump updated_at so the site's row tracks the workspace's last change."""
    _execute(
        "UPDATE webpage_sites SET updated_at = %(now)s"
        " WHERE chat_id = %(chat_id)s AND user_id = %(user_id)s",
        {"now": _now(), "chat_id": chat_id, "user_id": user_id},
    )
