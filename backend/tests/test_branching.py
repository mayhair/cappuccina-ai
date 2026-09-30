"""Chat branching: message trees, sibling order and reply parenting."""

import sqlite3
from types import SimpleNamespace

from app import db


def _user_and_chat():
    user = db.create_google_user("branch@example.com", "Branch", "google-sub-branch")
    chat = db.create_chat(user["id"], title="Branchy")
    return user, chat


def _node(uid, parent, branch, role, content):
    return {
        "uid": uid,
        "parent_uid": parent,
        "branch_index": branch,
        "role": role,
        "content": content,
        "reasoning": "",
        "tools": [],
        "parts": [],
        "thinking_done": False,
        "thought_seconds": None,
    }


def _stored(chat_id):
    return {m["id"]: m for m in db.list_messages_by_chat([chat_id])[chat_id]}


def test_replace_messages_round_trips_tree(isolated_db):
    _, chat = _user_and_chat()
    db.replace_chat_messages(
        chat["id"],
        [
            _node("u1", None, 0, "user", "hi"),
            _node("a1", "u1", 0, "assistant", "hello"),
            _node("u2", "u1", 1, "user", "hey again"),
        ],
    )

    by_id = _stored(chat["id"])
    assert by_id["u1"]["parentId"] is None
    assert by_id["a1"]["parentId"] == "u1"
    assert by_id["a1"]["branchIndex"] == 0
    assert by_id["u2"]["parentId"] == "u1"
    assert by_id["u2"]["branchIndex"] == 1
    assert by_id["u2"]["role"] == "user"


def test_replace_messages_is_idempotent(isolated_db):
    _, chat = _user_and_chat()
    tree = [_node("u1", None, 0, "user", "hi"), _node("a1", "u1", 0, "assistant", "yo")]
    db.replace_chat_messages(chat["id"], tree)
    db.replace_chat_messages(chat["id"], tree)

    messages = db.list_messages_by_chat([chat["id"]])[chat["id"]]
    assert [m["id"] for m in messages] == ["u1", "a1"]


def test_append_message_parents_and_orders_siblings(isolated_db):
    _, chat = _user_and_chat()
    db.replace_chat_messages(chat["id"], [_node("u1", None, 0, "user", "hi")])

    db.append_chat_message(
        chat["id"], role="assistant", content="first", uid="a1", parent_uid="u1"
    )
    db.append_chat_message(
        chat["id"], role="assistant", content="second", uid="a2", parent_uid="u1"
    )

    by_id = _stored(chat["id"])
    assert (by_id["a1"]["parentId"], by_id["a1"]["branchIndex"]) == ("u1", 0)
    assert (by_id["a2"]["parentId"], by_id["a2"]["branchIndex"]) == ("u1", 1)


def test_append_without_parent_is_a_root(isolated_db):
    _, chat = _user_and_chat()
    db.append_chat_message(chat["id"], role="assistant", content="loose", uid="a0")

    message = db.list_messages_by_chat([chat["id"]])[chat["id"]][0]
    assert message["parentId"] is None
    assert message["branchIndex"] == 0


def test_legacy_rows_backfill_into_one_branch(tmp_path, monkeypatch):
    """A pre-branching DB gains the columns and links its rows linearly."""
    legacy = sqlite3.connect(tmp_path / "legacy.db")
    legacy.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            reasoning TEXT NOT NULL DEFAULT '',
            tools TEXT NOT NULL DEFAULT '[]',
            parts TEXT NOT NULL DEFAULT '[]',
            thinking_done INTEGER NOT NULL DEFAULT 0,
            thought_seconds REAL,
            created_at TEXT NOT NULL
        );
        """
    )
    for content in ("one", "two", "three"):
        legacy.execute(
            "INSERT INTO messages (chat_id, role, content, created_at)"
            " VALUES ('c1', 'user', ?, '2020-01-01T00:00:00+00:00')",
            (content,),
        )
    legacy.commit()

    legacy.row_factory = sqlite3.Row
    db._init_schema(legacy)

    cols = {row[1] for row in legacy.execute("PRAGMA table_info(messages)")}
    assert {"uid", "parent_uid", "branch_index"} <= cols
    rows = legacy.execute(
        "SELECT uid, parent_uid, branch_index FROM messages ORDER BY id"
    ).fetchall()
    assert [r["uid"] for r in rows] == ["m1", "m2", "m3"]
    assert rows[0]["parent_uid"] is None
    assert rows[1]["parent_uid"] == "m1"
    assert rows[2]["parent_uid"] == "m2"
    assert all(r["branch_index"] == 0 for r in rows)
    legacy.close()


def test_sync_history_returns_last_visible_uid(isolated_db):
    import app.main as main

    _, chat = _user_and_chat()
    req = main.ChatRequest(
        chatId=chat["id"],
        messages=[
            {"id": "u1", "parentId": None, "branchIndex": 0, "role": "user", "content": "q1"},
            {"id": "u2", "parentId": "u1", "branchIndex": 1, "role": "user", "content": "q2"},
        ],
        tree=[
            {"id": "u1", "parentId": None, "branchIndex": 0, "role": "user", "content": "q1"},
            {"id": "a1", "parentId": "u1", "branchIndex": 0, "role": "assistant", "content": "old"},
            {"id": "u2", "parentId": "u1", "branchIndex": 1, "role": "user", "content": "q2"},
        ],
    )
    assert main._sync_history(chat["id"], req) == "u2"


def test_sync_history_legacy_payload_is_linear(isolated_db):
    import app.main as main

    _, chat = _user_and_chat()
    req = main.ChatRequest(
        chatId=chat["id"],
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    )
    last = main._sync_history(chat["id"], req)
    rows = db.list_messages_by_chat([chat["id"]])[chat["id"]]
    assert [m["id"] for m in rows] == ["legacy-0", "legacy-1"]
    assert rows[1]["parentId"] == "legacy-0"
    assert last == "legacy-1"


def test_stream_syncs_tree_and_parents_reply(signed_in, monkeypatch):
    import app.main as main

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            yield {"event": "on_chat_model_start", "data": {}}
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="fresh reply")},
            }

    monkeypatch.setattr(main, "GRAPH", FakeGraph())
    res = signed_in.post(
        "/api/v1/chat/stream",
        json={
            "chatId": chat["id"],
            "messages": [
                {"id": "u1", "parentId": None, "branchIndex": 0, "role": "user", "content": "q1"},
                {"id": "u2", "parentId": "u1", "branchIndex": 1, "role": "user", "content": "q2"},
            ],
            "tree": [
                {"id": "u1", "parentId": None, "branchIndex": 0, "role": "user", "content": "q1"},
                {"id": "a1", "parentId": "u1", "branchIndex": 0, "role": "assistant", "content": "old"},
                {"id": "u2", "parentId": "u1", "branchIndex": 1, "role": "user", "content": "q2"},
            ],
            "replyId": "a2",
            "generateTitle": False,
        },
    )
    assert res.status_code == 200

    by_id = _stored(chat["id"])
    assert {"u1", "a1", "u2", "a2"} <= set(by_id)
    # Both branches survive; the reply hangs off the edited branch.
    assert by_id["u2"]["branchIndex"] == 1
    assert by_id["a2"]["parentId"] == "u2"
    assert by_id["a2"]["branchIndex"] == 0
    assert by_id["a2"]["content"] == "fresh reply"


def test_regenerate_creates_a_sibling_version(signed_in, monkeypatch):
    import app.main as main

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    db.replace_chat_messages(
        chat["id"],
        [
            _node("u1", None, 0, "user", "q1"),
            _node("a1", "u1", 0, "assistant", "first answer"),
        ],
    )

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            yield {"event": "on_chat_model_start", "data": {}}
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="second answer")},
            }

    monkeypatch.setattr(main, "GRAPH", FakeGraph())
    # Regenerate: the visible path is just the prompt; the reply becomes a
    # sibling of a1 under u1.
    res = signed_in.post(
        "/api/v1/chat/stream",
        json={
            "chatId": chat["id"],
            "messages": [
                {"id": "u1", "parentId": None, "branchIndex": 0, "role": "user", "content": "q1"},
            ],
            "tree": [
                {"id": "u1", "parentId": None, "branchIndex": 0, "role": "user", "content": "q1"},
                {"id": "a1", "parentId": "u1", "branchIndex": 0, "role": "assistant", "content": "first answer"},
            ],
            "replyId": "a2",
            "generateTitle": False,
        },
    )
    assert res.status_code == 200

    by_id = _stored(chat["id"])
    assert (by_id["a1"]["parentId"], by_id["a1"]["branchIndex"]) == ("u1", 0)
    assert (by_id["a2"]["parentId"], by_id["a2"]["branchIndex"]) == ("u1", 1)


def test_active_branch_round_trips_through_api(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()

    res = signed_in.patch(
        f"/api/v1/chats/{chat['id']}/active-branch", json={"leafId": "a2"}
    )
    assert res.status_code == 200
    stored = signed_in.get("/api/v1/chats").json()
    assert stored[0]["activeLeafId"] == "a2"

    # Clearing is allowed too.
    signed_in.patch(
        f"/api/v1/chats/{chat['id']}/active-branch", json={"leafId": None}
    )
    assert signed_in.get("/api/v1/chats").json()[0]["activeLeafId"] is None


def test_active_branch_is_user_scoped(signed_in):
    stranger = db.create_google_user("stranger2@example.com", "Stranger", "sub-y")
    chat = db.create_chat(stranger["id"], title="Theirs")

    res = signed_in.patch(
        f"/api/v1/chats/{chat['id']}/active-branch", json={"leafId": "x"}
    )
    assert res.status_code == 404
    assert db.get_chat(stranger["id"], chat["id"])["active_leaf_uid"] is None


def test_stream_records_reply_as_active_leaf(signed_in, monkeypatch):
    import app.main as main

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            yield {"event": "on_chat_model_start", "data": {}}
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="hi there")},
            }

    monkeypatch.setattr(main, "GRAPH", FakeGraph())
    res = signed_in.post(
        "/api/v1/chat/stream",
        json={
            "chatId": chat["id"],
            "messages": [
                {"id": "u1", "parentId": None, "branchIndex": 0, "role": "user", "content": "q1"},
            ],
            "tree": [
                {"id": "u1", "parentId": None, "branchIndex": 0, "role": "user", "content": "q1"},
            ],
            "replyId": "a1",
            "generateTitle": False,
        },
    )
    assert res.status_code == 200
    stored = signed_in.get("/api/v1/chats").json()
    assert stored[0]["activeLeafId"] == "a1"
