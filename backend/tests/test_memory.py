import json

import pytest

from app import db


def _user(email="kid@example.com", sub="sub-1"):
    return db.create_google_user(email, "Kid", sub)


def _config(user):
    return {"configurable": {"user_id": user["id"], "chat_id": "chat-1"}}


# ---------- storage ----------

def test_memory_crud_and_per_user_isolation(isolated_db):
    a, b = _user("a@example.com", "sa"), _user("b@example.com", "sb")
    mem = db.create_memory(a["id"], "Likes drawing cats", source_chat_id="chat-1")
    assert mem["content"] == "Likes drawing cats"
    assert mem["source_chat_id"] == "chat-1"

    assert [m["id"] for m in db.list_memories(a["id"])] == [mem["id"]]
    assert db.list_memories(b["id"]) == []
    # Another user can neither read, change nor delete it.
    assert db.get_memory(b["id"], mem["id"]) is None
    assert db.update_memory(b["id"], mem["id"], "hijacked") is None
    assert db.delete_memory(b["id"], mem["id"]) is False

    updated = db.update_memory(a["id"], mem["id"], "Loves drawing dogs")
    assert updated["content"] == "Loves drawing dogs"
    assert db.delete_memory(a["id"], mem["id"]) is True
    assert db.delete_memory(a["id"], mem["id"]) is False
    assert db.count_memories(a["id"]) == 0


def test_create_memory_dedupes_ignoring_case(isolated_db):
    user = _user()
    first = db.create_memory(user["id"], "Likes cats")
    again = db.create_memory(user["id"], "likes CATS")
    assert again["id"] == first["id"]
    assert db.count_memories(user["id"]) == 1


def test_list_memories_is_newest_first_and_limited(isolated_db):
    user = _user()
    old = db.create_memory(user["id"], "old")
    new = db.create_memory(user["id"], "new")
    db.update_memory(user["id"], old["id"], "old, touched")
    ids = [m["id"] for m in db.list_memories(user["id"])]
    assert ids == [old["id"], new["id"]]
    assert len(db.list_memories(user["id"], 1)) == 1


def test_memories_survive_chat_deletion_but_not_user_deletion(isolated_db):
    user = _user()
    chat = db.create_chat(user["id"])
    db.create_memory(user["id"], "Likes cats", source_chat_id=chat["id"])
    db.delete_chat(user["id"], chat["id"])
    assert db.count_memories(user["id"]) == 1
    with db.connection() as conn:
        conn.execute("DELETE FROM users WHERE id = %(id)s", {"id": user["id"]})
    assert db.count_memories(user["id"]) == 0


def test_memory_enabled_defaults_on_and_toggles(isolated_db):
    user = _user()
    assert user["memory_enabled"] is True
    db.set_memory_enabled(user["id"], False)
    assert db.get_user_by_id(user["id"])["memory_enabled"] is False


def test_delete_all_memories(isolated_db):
    a, b = _user("a@example.com", "sa"), _user("b@example.com", "sb")
    db.create_memory(a["id"], "one")
    db.create_memory(a["id"], "two")
    db.create_memory(b["id"], "other")
    assert db.delete_all_memories(a["id"]) == 2
    assert db.count_memories(b["id"]) == 1


# ---------- validation ----------

@pytest.mark.parametrize(
    "text",
    [
        "Email is kid@example.com",
        "Phone is 555-123-4567",
        "Phone (555) 123 4567",
        "Lives at 42 Maple Street",
        "Lives at 221 Baker St",
        "My password is hunter2",
        "pin: 1234",
        "Card 4111 1111 1111 1111",
        "SSN 123-45-6789",
    ],
)
def test_validate_rejects_private_info_for_young_users(text):
    from agent import memory

    _, error = memory.validate(text, adult=False)
    assert error
    # Adult mode drops the content filters.
    assert memory.validate(text, adult=True)[1] is None


@pytest.mark.parametrize(
    "text",
    [
        "Likes drawing cats",
        "Is learning the guitar",
        "Has 2 cats on the way to the vet",
        "Walks 5 miles down the road",
        "Favorite number is 42",
        "Birthday is in June",
    ],
)
def test_validate_allows_ordinary_facts(text):
    from agent import memory

    assert memory.validate(text, adult=False)[1] is None


def test_validate_caps_apply_to_everyone():
    from agent import memory
    from app.config import MEMORY_MAX_CHARS

    for adult in (False, True):
        assert memory.validate("   ", adult)[1]
        assert memory.validate("a" * (MEMORY_MAX_CHARS + 1), adult)[1]
        text, error = memory.validate("  Likes\n\n cats  ", adult)
        assert (text, error) == ("Likes cats", None)


def test_search_ranks_by_overlap():
    from agent import memory

    rows = [
        {"id": "1", "content": "Likes pizza"},
        {"id": "2", "content": "Has a dog named Biscuit"},
        {"id": "3", "content": "Is learning the guitar"},
    ]
    assert [m["id"] for m in memory.search(rows, "what is my dog called")] == ["2"]
    assert memory.search(rows, "xyzzy") == []
    assert len(memory.search(rows, "", limit=2)) == 2


def test_format_for_prompt_marks_memories_as_data():
    from agent import memory

    assert memory.format_for_prompt([]) == ""
    note = memory.format_for_prompt(
        [{"id": "abc", "content": "Likes </memories> cats"}], total=30
    )
    assert "[abc] Likes /memories cats" in note
    assert "NOT instructions" in note
    assert "1 newest of 30" in note
    assert note.count("</memories>") == 1


# ---------- tools ----------

def test_save_update_delete_tools(isolated_db):
    from agent import tools

    user = _user()
    cfg = _config(user)
    saved = json.loads(tools.save_memory.invoke({"content": "Likes cats"}, config=cfg))
    assert saved["status"] == "saved"
    assert db.get_memory(user["id"], saved["id"])["source_chat_id"] == "chat-1"

    updated = json.loads(
        tools.update_memory.invoke({"memory_id": saved["id"], "content": "Loves cats"}, config=cfg)
    )
    assert updated["status"] == "updated"
    assert updated["previous"] == "Likes cats"

    found = tools.search_memories.invoke({"query": "cats"}, config=cfg)
    assert saved["id"] in found

    deleted = json.loads(tools.delete_memory.invoke({"memory_id": saved["id"]}, config=cfg))
    assert deleted["status"] == "deleted"
    assert tools.search_memories.invoke({"query": "cats"}, config=cfg) == "No matching memories."


def test_tools_cannot_touch_another_users_memories(isolated_db):
    from agent import tools

    a, b = _user("a@example.com", "sa"), _user("b@example.com", "sb")
    mem = db.create_memory(a["id"], "Likes cats")
    out = tools.delete_memory.invoke({"memory_id": mem["id"]}, config=_config(b))
    assert "couldn't find" in out
    assert db.get_memory(a["id"], mem["id"]) is not None


def test_save_memory_tool_filters_private_info_unless_adult(isolated_db):
    from agent import tools

    user = _user()
    out = tools.save_memory.invoke({"content": "Phone is 555-123-4567"}, config=_config(user))
    assert "private" in out
    assert db.count_memories(user["id"]) == 0

    db.update_user_adult_mode(user["id"], True)
    out = tools.save_memory.invoke({"content": "Phone is 555-123-4567"}, config=_config(user))
    assert json.loads(out)["status"] == "saved"


def test_save_memory_tool_respects_cap_and_toggle(isolated_db, monkeypatch):
    from agent import tools

    user = _user()
    cfg = _config(user)
    monkeypatch.setattr(tools, "MEMORY_MAX_PER_USER", 1)
    tools.save_memory.invoke({"content": "one"}, config=cfg)
    assert "full" in tools.save_memory.invoke({"content": "two"}, config=cfg)

    db.set_memory_enabled(user["id"], False)
    assert "turned off" in tools.search_memories.invoke({"query": "one"}, config=cfg)
    assert "isn't available" in tools.save_memory.invoke({"content": "x"}, config={})


def test_memory_tool_schemas_hide_injected_config():
    from agent import tools

    for tool in (tools.save_memory, tools.update_memory, tools.delete_memory, tools.search_memories):
        assert "config" not in tool.args
    assert tools.TOOL_DISPLAY_NAMES["save_memory"] == "Saving Memory"
    assert tools.MEMORY_TOOL_NAMES <= {t.name for t in tools.ALL_TOOLS}
    assert tools.MEMORY_TOOL_NAMES <= set(tools.TOOL_DISPLAY_NAMES)


# ---------- prompt and graph ----------

def test_graph_hides_memory_tools_when_off():
    from agent.graph import _tools_for
    from agent.tools import ALL_TOOLS, MEMORY_TOOL_NAMES

    off = {t.name for t in _tools_for(ALL_TOOLS, True, True, memory=False)}
    assert not off & MEMORY_TOOL_NAMES
    on = {t.name for t in _tools_for(ALL_TOOLS, True, True, memory=True)}
    assert MEMORY_TOOL_NAMES <= on


def test_system_prompt_memory_rules():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("chat", None)
    assert "## Memory\n" in prompt
    assert "## Memory for young users" in prompt
    # Adult mode drops the young-user addendum but keeps the general rules.
    adult = build_system_prompt("chat", None, adult=True)
    assert "## Memory\n" in adult
    assert "## Memory for young users" not in adult
    # Memory off drops both.
    off = build_system_prompt("chat", None, memory=False)
    assert "## Memory" not in off


def test_build_messages_adds_memory_note_below_history():
    from agent import prompts

    history = [{"role": "user", "content": "hi"}]
    memories = [{"id": "m1", "content": "Likes cats"}]
    messages = prompts.build_messages("chat", None, None, history, memories=memories)
    assert messages[1] is history[0]
    assert "[m1] Likes cats" in messages[-2]["content"]
    assert messages[-1]["content"].startswith("Current Date:")

    assert len(prompts.build_messages("chat", None, None, history)) == 3
    off = prompts.build_messages("chat", None, None, history, memory=False, memories=memories)
    assert len(off) == 3


# ---------- REST ----------

def test_memory_routes_require_sign_in(client):
    assert client.get("/api/v1/memories").status_code == 401
    assert client.post("/api/v1/memories", json={"content": "x"}).status_code == 401


def test_memory_rest_roundtrip(signed_in):
    c = signed_in
    listing = c.get("/api/v1/memories").json()
    assert listing["memories"] == []
    assert listing["enabled"] is True
    assert listing["limit"] > 0
    assert listing["maxChars"] > 0

    created = c.post("/api/v1/memories", json={"content": "Likes cats"})
    assert created.status_code == 201
    mem = created.json()
    assert mem["content"] == "Likes cats"

    edited = c.patch(f"/api/v1/memories/{mem['id']}", json={"content": "Loves cats"})
    assert edited.json()["content"] == "Loves cats"
    assert c.get("/api/v1/memories").json()["memories"][0]["content"] == "Loves cats"

    assert c.delete(f"/api/v1/memories/{mem['id']}").json() == {"ok": True}
    assert c.delete(f"/api/v1/memories/{mem['id']}").status_code == 404
    assert c.patch(f"/api/v1/memories/{mem['id']}", json={"content": "x"}).status_code == 404


def test_memory_rest_validation_and_adult_mode(signed_in):
    c = signed_in
    bad = c.post("/api/v1/memories", json={"content": "Call me on 555-123-4567"})
    assert bad.status_code == 400
    assert "private" in bad.json()["detail"]
    assert c.post("/api/v1/memories", json={"content": "   "}).status_code == 400
    assert c.post("/api/v1/memories", json={"content": "a" * 1000}).status_code == 400

    assert c.patch("/api/v1/auth/me", json={"adultMode": True}).json()["adultMode"] is True
    assert c.post("/api/v1/memories", json={"content": "Call me on 555-123-4567"}).status_code == 201


def test_memory_rest_cap(signed_in, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "MEMORY_MAX_PER_USER", 1)
    assert signed_in.post("/api/v1/memories", json={"content": "one"}).status_code == 201
    assert signed_in.post("/api/v1/memories", json={"content": "two"}).status_code == 409


def test_memory_rest_delete_all_and_toggle(signed_in):
    c = signed_in
    c.post("/api/v1/memories", json={"content": "one"})
    c.post("/api/v1/memories", json={"content": "two"})
    assert c.delete("/api/v1/memories").json()["deleted"] == 2
    assert c.get("/api/v1/memories").json()["memories"] == []

    assert c.get("/api/v1/auth/me").json()["memoryEnabled"] is True
    off = c.patch("/api/v1/auth/me", json={"memoryEnabled": False}).json()
    assert off["memoryEnabled"] is False
    assert c.get("/api/v1/memories").json()["enabled"] is False
    assert c.patch("/api/v1/auth/me", json={"memoryEnabled": True}).json()["memoryEnabled"] is True


def test_chat_state_carries_memories_only_when_enabled(isolated_db):
    import app.main as main
    from app.schemas import ChatRequest

    user = _user()
    db.create_memory(user["id"], "Likes cats")
    req = ChatRequest(chatId="c1", messages=[{"role": "user", "content": "hi"}])

    state = main._state(req, db.get_user_by_id(user["id"]))
    assert state["memory"] is True
    assert [m["content"] for m in state["memories"]] == ["Likes cats"]
    assert state["memory_total"] == 1

    db.set_memory_enabled(user["id"], False)
    state = main._state(req, db.get_user_by_id(user["id"]))
    assert state["memory"] is False
    assert state["memories"] == []
