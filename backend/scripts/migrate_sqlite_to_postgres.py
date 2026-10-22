"""One-off copy of the old SQLite database into PostgreSQL.

Run from backend/ (so ``app`` is importable and .env is found):

    python -m scripts.migrate_sqlite_to_postgres --sqlite data/cappuccina.db

The target is DATABASE_URL (see app/config.py). The script applies the schema
migrations first, then copies all 12 tables parent-first in ONE transaction:
either every table is copied or nothing is.

* It refuses to run if any target table already holds rows, unless
  --force-truncate is given (that empties all 12 tables first).
* Types are converted: ISO-8601 text -> timestamptz (naive values are taken as
  UTC), 0/1 -> boolean, JSON text -> jsonb. Legacy NULLs and odd values are
  repaired with defaults instead of aborting; the number of repairs is printed.
* messages.id values are kept, then the identity sequence is moved past them.
* Rows whose parent is missing (they would violate a foreign key) are skipped
  and reported, not copied.
* SQLite is opened read-only. On a server, take a consistent copy first, while
  the service is stopped, and migrate the copy:

      sqlite3 cappuccina.db ".backup /tmp/cappuccina-copy.db"

Images, attachments and website folders live on disk and are not touched.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from psycopg.types.json import Jsonb

# Column kinds: how a SQLite value becomes a PostgreSQL one.
TEXT, TS, BOOL, JSON, INT, FLOAT = "text", "ts", "bool", "json", "int", "float"

# (column, kind, default used when the value is NULL/unusable or the column is
# missing from an old SQLite file). A default of None means the column is
# nullable; TS columns that are NOT NULL fall back to "now".
Spec = tuple[str, str, Any]

TABLES: dict[str, list[Spec]] = {
    "users": [
        ("id", TEXT, None), ("email", TEXT, None), ("display_name", TEXT, ""),
        ("password_hash", TEXT, None), ("google_sub", TEXT, None),
        ("avatar_url", TEXT, None), ("adult_mode", BOOL, False),
        ("memory_enabled", BOOL, True), ("created_at", TS, "now"),
    ],
    "sessions": [
        ("token", TEXT, None), ("user_id", TEXT, None), ("created_at", TS, "now"),
        ("expires_at", TS, "now"),
    ],
    "chats": [
        ("id", TEXT, None), ("user_id", TEXT, None), ("title", TEXT, "New chat"),
        ("mode", TEXT, "chat"), ("subject", TEXT, None),
        ("active_leaf_uid", TEXT, None), ("created_at", TS, "now"),
        ("updated_at", TS, "now"),
    ],
    "messages": [
        ("id", INT, None), ("chat_id", TEXT, None), ("uid", TEXT, None),
        ("parent_uid", TEXT, None), ("branch_index", INT, 0), ("role", TEXT, "user"),
        ("content", TEXT, ""), ("reasoning", TEXT, ""), ("tools", JSON, []),
        ("parts", JSON, []), ("thinking_done", BOOL, False),
        ("thought_seconds", FLOAT, None), ("created_at", TS, "now"),
    ],
    "google_grants": [
        ("user_id", TEXT, None), ("service", TEXT, "gmail"), ("scopes", TEXT, ""),
        ("access_token", TEXT, ""), ("refresh_token", TEXT, ""),
        ("expires_at", TS, None), ("granted_at", TS, "now"), ("updated_at", TS, "now"),
    ],
    "oauth_states": [
        ("token", TEXT, None), ("purpose", TEXT, ""), ("user_id", TEXT, None),
        ("created_at", TS, "now"), ("expires_at", TS, "now"),
    ],
    "sent_drafts": [
        ("user_id", TEXT, None), ("draft_id", TEXT, None), ("message_id", TEXT, ""),
        ("sent_at", TS, "now"),
    ],
    "deleted_events": [
        ("user_id", TEXT, None), ("event_id", TEXT, None), ("deleted_at", TS, "now"),
    ],
    "images": [
        ("id", TEXT, None), ("user_id", TEXT, None), ("chat_id", TEXT, None),
        ("mime", TEXT, "image/png"), ("size_bytes", INT, 0), ("width", INT, None),
        ("height", INT, None), ("prompt", TEXT, ""), ("created_at", TS, "now"),
    ],
    "attachments": [
        ("id", TEXT, None), ("user_id", TEXT, None), ("chat_id", TEXT, None),
        ("filename", TEXT, ""), ("mime", TEXT, ""), ("kind", TEXT, ""),
        ("size_bytes", INT, 0), ("text", TEXT, ""), ("char_count", INT, 0),
        ("created_at", TS, "now"),
    ],
    "webpage_sites": [
        ("id", TEXT, None), ("user_id", TEXT, None), ("chat_id", TEXT, None),
        ("preview_key", TEXT, ""), ("active_commit", TEXT, None),
        ("created_at", TS, "now"), ("updated_at", TS, "now"),
    ],
    "memories": [
        ("id", TEXT, None), ("user_id", TEXT, None), ("content", TEXT, ""),
        ("source_chat_id", TEXT, None), ("created_at", TS, "now"),
        ("updated_at", TS, "now"),
    ],
}

# Foreign keys: table -> [(column, parent table)]. The parent's primary key is
# `id` for all of them. oauth_states.user_id and memories.source_chat_id have
# no foreign key on purpose.
FOREIGN_KEYS: dict[str, list[tuple[str, str]]] = {
    "sessions": [("user_id", "users")],
    "chats": [("user_id", "users")],
    "messages": [("chat_id", "chats")],
    "google_grants": [("user_id", "users")],
    "sent_drafts": [("user_id", "users")],
    "deleted_events": [("user_id", "users")],
    "images": [("user_id", "users"), ("chat_id", "chats")],
    "attachments": [("user_id", "users"), ("chat_id", "chats")],
    "webpage_sites": [("user_id", "users"), ("chat_id", "chats")],
    "memories": [("user_id", "users")],
}

# Only tables whose primary key other tables point at need their ids tracked.
PARENTS = {"users", "chats"}

BATCH = 500


class Repairs:
    """Counts of values that had to be defaulted, per table and column."""

    def __init__(self) -> None:
        self.counts: dict[tuple[str, str], int] = {}

    def note(self, table: str, column: str) -> None:
        self.counts[(table, column)] = self.counts.get((table, column), 0) + 1


def _strip_nul(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    if isinstance(value, dict):
        return {_strip_nul(k): _strip_nul(v) for k, v in value.items()}
    return value


def _to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).strip())
    except (ValueError, OverflowError, OSError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "t", "yes", "on"):
            return True
        if lowered in ("0", "false", "f", "no", "off", ""):
            return False
        return None
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None


def _to_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    try:
        return json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    return _strip_nul(str(value))


_CONVERTERS: dict[str, Callable[[Any], Any]] = {
    TEXT: _to_text, TS: _to_datetime, BOOL: _to_bool, JSON: _to_json,
    INT: _to_int, FLOAT: _to_float,
}


def convert_row(table: str, raw: dict[str, Any], now: datetime, repairs: Repairs) -> dict[str, Any]:
    """One SQLite row (as a dict) -> the PostgreSQL row, repairing odd values."""
    out: dict[str, Any] = {}
    for column, kind, default in TABLES[table]:
        value = _CONVERTERS[kind](raw.get(column))
        if value is None:
            if default == "now":
                value = now
                repairs.note(table, column)
            elif default is not None:
                value = default
                if raw.get(column) is not None or column in raw:
                    repairs.note(table, column)  # NULL/odd in the file, not merely absent
            elif raw.get(column) is not None:
                repairs.note(table, column)  # unparseable, stored as NULL
        if kind == JSON and value is not None:
            value = Jsonb(_strip_nul(value))
        out[column] = value
    return out


def open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    """Open the file read-only. A WAL-mode file may need immutable mode when
    its directory is read-only; that is fine for a ``.backup`` copy."""
    if not path.is_file():
        raise SystemExit(f"SQLite file not found: {path}")
    uri = path.resolve().as_uri()
    try:
        conn = sqlite3.connect(f"{uri}?mode=ro", uri=True)
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.OperationalError:
        conn = sqlite3.connect(f"{uri}?immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _read_rows(conn: sqlite3.Connection, table: str):
    order = "ORDER BY chat_id, id" if table == "messages" else ""
    cursor = conn.execute(f'SELECT * FROM "{table}" {order}')
    for row in cursor:
        yield dict(row)


def migrate(
    sqlite_path: Path,
    *,
    force_truncate: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, dict[str, int]]:
    """Copy every table. Returns {table: {"sqlite": n, "postgres": n, "skipped": n}}."""
    # Imported here so --help works without a database.
    from app import db

    src = open_sqlite_readonly(sqlite_path)
    try:
        present = _sqlite_tables(src)
        db.get_pool()  # opens the pool and applies the schema migrations
        report: dict[str, dict[str, int]] = {}
        repairs = Repairs()
        now = datetime.now(timezone.utc)
        known_ids: dict[str, set[str]] = {name: set() for name in PARENTS}
        orphans: dict[str, list[str]] = {}

        with db.connection() as conn:
            filled = [
                t for t in TABLES
                if conn.execute(f'SELECT EXISTS (SELECT 1 FROM "{t}") AS e').fetchone()["e"]
            ]
            if filled and not force_truncate:
                raise SystemExit(
                    "Target database is not empty (" + ", ".join(filled) + "). "
                    "Nothing was copied. Pass --force-truncate to empty it first."
                )
            if filled:
                log("Truncating target tables: " + ", ".join(filled))
                conn.execute(
                    "TRUNCATE " + ", ".join(f'"{t}"' for t in TABLES) + " RESTART IDENTITY CASCADE"
                )

            for table in TABLES:
                stats = {"sqlite": 0, "postgres": 0, "skipped": 0}
                report[table] = stats
                if table not in present:
                    log(f"  {table}: not in the SQLite file, nothing to copy")
                    continue
                columns = [c for c, _k, _d in TABLES[table]]
                insert = (
                    f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES '
                    f'({", ".join("%(" + c + ")s" for c in columns)})'
                )
                batch: list[dict[str, Any]] = []
                prev_chat: str | None = None
                prev_uid: str | None = None

                def flush() -> None:
                    if batch:
                        with conn.cursor() as cur:
                            cur.executemany(insert, batch)
                        stats["postgres"] += len(batch)
                        batch.clear()

                for raw in _read_rows(src, table):
                    stats["sqlite"] += 1
                    row = convert_row(table, raw, now, repairs)
                    if table == "messages":
                        # Pre-branching rows have no uid: rebuild the linear chain.
                        if row["chat_id"] != prev_chat:
                            prev_chat, prev_uid = row["chat_id"], None
                        if not row["uid"]:
                            row["uid"] = f"m{row['id']}"
                            row["parent_uid"] = prev_uid
                            repairs.note(table, "uid")
                        prev_uid = row["uid"]
                    missing = [
                        f"{col}={row[col]!r} not in {parent}"
                        for col, parent in FOREIGN_KEYS.get(table, [])
                        if row[col] not in known_ids[parent]
                    ]
                    if missing:
                        stats["skipped"] += 1
                        orphans.setdefault(table, []).append("; ".join(missing))
                        continue
                    if table in PARENTS:
                        known_ids[table].add(row["id"])
                    batch.append(row)
                    if len(batch) >= BATCH:
                        flush()
                flush()
                log(f"  {table}: copied {stats['postgres']} of {stats['sqlite']}")

            # Keep generated ids ahead of the ones we copied.
            conn.execute(
                "SELECT setval(pg_get_serial_sequence('messages', 'id'),"
                " COALESCE((SELECT MAX(id) FROM messages), 1),"
                " EXISTS (SELECT 1 FROM messages))"
            )
            for table, stats in report.items():
                stats["postgres"] = conn.execute(
                    f'SELECT COUNT(*) AS n FROM "{table}"'
                ).fetchone()["n"]

        _print_summary(report, repairs, orphans, log)
        return report
    finally:
        src.close()


def _print_summary(report, repairs: Repairs, orphans: dict[str, list[str]], log) -> None:
    log("")
    log(f"{'table':<16}{'sqlite':>10}{'postgres':>10}{'skipped':>10}")
    for table, s in report.items():
        flag = "" if s["sqlite"] == s["postgres"] + s["skipped"] else "   <-- MISMATCH"
        log(f"{table:<16}{s['sqlite']:>10}{s['postgres']:>10}{s['skipped']:>10}{flag}")
    if repairs.counts:
        log("")
        log("Values repaired with defaults (NULL or unparseable in SQLite):")
        for (table, column), n in sorted(repairs.counts.items()):
            log(f"  {table}.{column}: {n}")
    for table, reasons in orphans.items():
        log("")
        log(f"Skipped {len(reasons)} orphan row(s) in {table} (would violate a foreign key):")
        for reason in reasons[:10]:
            log(f"  {reason}")
        if len(reasons) > 10:
            log(f"  ... and {len(reasons) - 10} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sqlite", required=True, type=Path, help="path to the SQLite file")
    parser.add_argument(
        "--force-truncate", action="store_true",
        help="empty the target tables first if they already hold rows",
    )
    args = parser.parse_args(argv)
    try:
        report = migrate(args.sqlite, force_truncate=args.force_truncate)
    finally:
        from app import db

        db.close_pool()
    clean = all(s["sqlite"] == s["postgres"] + s["skipped"] for s in report.values())
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
