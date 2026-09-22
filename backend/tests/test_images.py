import json

from app import db

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def _user_and_chat():
    user = db.create_google_user("artist@example.com", "Artist", "google-sub-art")
    chat = db.create_chat(user["id"], title="Draw things")
    return user, chat


def test_generate_image_tool_saves_file_and_row(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    monkeypatch.setattr(
        tools, "_openai_generate_image", lambda prompt, size: (FAKE_PNG, "image/png")
    )

    out = tools.generate_image.invoke(
        {"prompt": "a cat in a tutu"},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    data = json.loads(out)
    assert data["status"] == "image_generated"
    assert data["prompt"] == "a cat in a tutu"
    assert data["width"] == 1024
    assert data["height"] == 1024

    row = db.get_image(user["id"], data["image_id"])
    assert row is not None
    assert row["chat_id"] == chat["id"]
    assert row["mime"] == "image/png"
    assert row["size_bytes"] == len(FAKE_PNG)
    assert db.image_file_path(row["id"], row["mime"]).read_bytes() == FAKE_PNG

    # Another user can't see it.
    assert db.get_image("someone-else", data["image_id"]) is None


def test_generate_image_tool_without_context(isolated_db):
    from agent import tools

    out = tools.generate_image.invoke(
        {"prompt": "a cat"}, config={"configurable": {}}
    )
    assert "isn't available" in out


def test_generate_image_schema_hides_injected_config():
    from agent import tools

    assert "config" not in tools.generate_image.args
    assert set(tools.generate_image.args) == {"prompt", "size"}


def test_generate_image_tool_reports_api_failure(isolated_db, monkeypatch):
    import httpx

    from agent import tools

    user, chat = _user_and_chat()

    def boom(prompt, size):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(tools, "_openai_generate_image", boom)
    out = tools.generate_image.invoke(
        {"prompt": "a cat"},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    assert "Couldn't draw that" in out


def test_generate_image_tool_selects_resolution(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    config = {"configurable": {"user_id": user["id"], "chat_id": chat["id"]}}
    seen = []

    def fake_generate(prompt, size):
        seen.append(size)
        return FAKE_PNG, "image/png"

    monkeypatch.setattr(tools, "_openai_generate_image", fake_generate)

    wide = json.loads(tools.generate_image.invoke(
        {"prompt": "a wide landscape", "size": "1536x1024"}, config=config
    ))
    assert wide["size"] == "1536x1024"
    assert (wide["width"], wide["height"]) == (1536, 1024)

    tall = json.loads(tools.generate_image.invoke(
        {"prompt": "a tall tower", "size": "1024x1536"}, config=config
    ))
    assert (tall["width"], tall["height"]) == (1024, 1536)

    # The multiplication sign is normalized; junk falls back to the default.
    tools.generate_image.invoke(
        {"prompt": "a cat", "size": "1536×1024"}, config=config
    )
    fallback = json.loads(tools.generate_image.invoke(
        {"prompt": "a cat", "size": "huge"}, config=config
    ))
    assert seen == ["1536x1024", "1024x1536", "1536x1024", "1024x1024"]
    assert fallback["size"] == "1024x1024"


def test_image_part_from_result():
    import app.main as main

    marker = json.dumps(
        {
            "status": "image_generated",
            "image_id": "img-1",
            "prompt": "a cat",
            "width": 1024,
            "height": 1024,
        }
    )
    part = main._image_part_from_result("generate_image", marker)
    assert part == {
        "type": "image",
        "imageId": "img-1",
        "prompt": "a cat",
        "width": 1024,
        "height": 1024,
    }
    # Other tools, failures and garbage never produce image parts.
    assert main._image_part_from_result("web_search", marker) is None
    assert main._image_part_from_result("generate_image", "nope") is None
    assert main._image_part_from_result("generate_image", "") is None
    assert (
        main._image_part_from_result(
            "generate_image", json.dumps({"status": "image_generated"})
        )
        is None
    )


def test_final_extras_appends_image_part():
    import app.main as main
    from langchain_core.messages import AIMessage, ToolMessage

    marker = json.dumps(
        {
            "status": "image_generated",
            "image_id": "img-9",
            "prompt": "a cat",
            "width": 1024,
            "height": 1024,
        }
    )
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "t1",
                    "name": "generate_image",
                    "args": {"prompt": "a cat"},
                }
            ],
        ),
        ToolMessage(content=marker, tool_call_id="t1"),
        AIMessage(content="Here you go!"),
    ]
    reasoning, tool_calls, parts = main._final_extras(messages)
    assert [p["type"] for p in parts] == ["tool", "image", "text"]
    assert parts[1]["imageId"] == "img-9"
    assert parts[2]["text"] == "Here you go!"
    assert tool_calls[0].result == marker


def test_serve_image_round_trip(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    image = db.create_image(
        user["id"], chat["id"], FAKE_PNG, prompt="a cat in a tutu"
    )

    res = signed_in.get(f"/api/v1/images/{image['id']}")
    assert res.status_code == 200
    assert res.content == FAKE_PNG
    assert res.headers["content-type"] == "image/png"

    assert signed_in.get("/api/v1/images/does-not-exist").status_code == 404


def test_serve_image_requires_login(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    image = db.create_image(user["id"], chat["id"], FAKE_PNG)

    assert signed_in.post("/api/v1/auth/logout").json() == {"ok": True}
    assert signed_in.get(f"/api/v1/images/{image['id']}").status_code == 401


def test_serve_image_is_user_scoped(signed_in):
    stranger = db.create_google_user("stranger@example.com", "Stranger", "sub-x")
    chat = db.create_chat(stranger["id"], title="Theirs")
    image = db.create_image(stranger["id"], chat["id"], FAKE_PNG)

    assert signed_in.get(f"/api/v1/images/{image['id']}").status_code == 404


def test_chat_delete_removes_image_rows_and_files(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    image = db.create_image(user["id"], chat["id"], FAKE_PNG)
    path = db.image_file_path(image["id"], image["mime"])
    assert path.is_file()

    assert signed_in.delete(f"/api/v1/chats/{chat['id']}").json() == {"ok": True}
    assert db.get_image(user["id"], image["id"]) is None
    assert not path.exists()
    assert signed_in.get(f"/api/v1/images/{image['id']}").status_code == 404


def test_stream_emits_image_part_and_persists_it(signed_in, monkeypatch):
    import app.main as main
    from types import SimpleNamespace

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    image = db.create_image(
        user["id"], chat["id"], FAKE_PNG, prompt="a cat in a tutu"
    )
    marker = json.dumps(
        {
            "status": "image_generated",
            "image_id": image["id"],
            "prompt": "a cat in a tutu",
            "width": 1024,
            "height": 1024,
        }
    )

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            assert config is not None
            assert config["configurable"]["chat_id"] == chat["id"]
            yield {"event": "on_chat_model_start", "data": {}}
            yield {
                "event": "on_tool_start",
                "run_id": "r1",
                "name": "generate_image",
                "data": {"input": {"prompt": "a cat in a tutu"}},
            }
            yield {
                "event": "on_tool_end",
                "run_id": "r1",
                "name": "generate_image",
                "data": {"output": marker},
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="Here you go!")},
            }

    monkeypatch.setattr(main, "GRAPH", FakeGraph())
    res = signed_in.post(
        "/api/v1/chat/stream",
        json={
            "chatId": chat["id"],
            "messages": [{"role": "user", "content": "draw a cat"}],
            "generateTitle": False,
        },
    )
    assert res.status_code == 200
    assert "event: image" in res.text
    assert image["id"] in res.text

    # The part survives a reload (cross-device viewing), and serves bytes.
    stored = signed_in.get("/api/v1/chats").json()
    assistant = stored[0]["messages"][-1]
    image_parts = [p for p in assistant["parts"] if p.get("type") == "image"]
    assert len(image_parts) == 1
    assert image_parts[0]["imageId"] == image["id"]
    assert image_parts[0]["prompt"] == "a cat in a tutu"

    served = signed_in.get(f"/api/v1/images/{image['id']}")
    assert served.status_code == 200
    assert served.content == FAKE_PNG


def test_nonstream_chat_persists_image_part(signed_in, monkeypatch):
    import app.main as main
    from langchain_core.messages import AIMessage, ToolMessage

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    image = db.create_image(user["id"], chat["id"], FAKE_PNG)
    marker = json.dumps(
        {
            "status": "image_generated",
            "image_id": image["id"],
            "prompt": "a cat",
            "width": 1024,
            "height": 1024,
        }
    )

    class FakeGraph:
        async def ainvoke(self, state, config=None):
            assert config is not None
            assert config["configurable"]["chat_id"] == chat["id"]
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "t1",
                                "name": "generate_image",
                                "args": {"prompt": "a cat"},
                            }
                        ],
                    ),
                    ToolMessage(content=marker, tool_call_id="t1"),
                    AIMessage(content="Done!"),
                ]
            }

    monkeypatch.setattr(main, "GRAPH", FakeGraph())
    res = signed_in.post(
        "/api/v1/chat",
        json={
            "chatId": chat["id"],
            "messages": [{"role": "user", "content": "draw a cat"}],
        },
    )
    assert res.status_code == 200

    stored = signed_in.get("/api/v1/chats").json()
    parts = stored[0]["messages"][-1]["parts"]
    assert [p for p in parts if p.get("type") == "image"][0]["imageId"] == image["id"]


# ---------- image_edit ----------


def test_image_edit_tool_saves_new_file_and_row(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    source = db.create_image(user["id"], chat["id"], FAKE_PNG, prompt="a cat")
    calls = []

    def fake_edit(prompt, image_bytes, filename, mime, size):
        calls.append((prompt, image_bytes, filename, mime, size))
        return b"edited-bytes", "image/png"

    monkeypatch.setattr(tools, "_openai_edit_image", fake_edit)

    out = tools.image_edit.invoke(
        {"prompt": "give it a hat", "image_id": source["id"]},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    data = json.loads(out)
    assert data["status"] == "image_edited"
    assert data["source_image_id"] == source["id"]
    assert data["prompt"] == "give it a hat"

    assert calls == [
        ("give it a hat", FAKE_PNG, f"{source['id']}.png", "image/png", "1024x1024")
    ]

    row = db.get_image(user["id"], data["image_id"])
    assert row is not None
    assert row["chat_id"] == chat["id"]
    assert row["size_bytes"] == len(b"edited-bytes")
    assert db.image_file_path(row["id"], row["mime"]).read_bytes() == b"edited-bytes"

    # The original picture is untouched (non-destructive edit).
    original = db.get_image(user["id"], source["id"])
    assert db.image_file_path(original["id"], original["mime"]).read_bytes() == FAKE_PNG


def test_image_edit_tool_from_attachment(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    attachment = db.create_attachment(
        user["id"], chat["id"], "photo.png", FAKE_PNG,
        kind="image", mime="image/png",
    )
    calls = []
    monkeypatch.setattr(
        tools,
        "_openai_edit_image",
        lambda prompt, image_bytes, filename, mime, size: (
            calls.append((image_bytes, filename, mime, size))
            or (b"edited", "image/png")
        ),
    )

    out = tools.image_edit.invoke(
        {
            "prompt": "remove the background",
            "attachment_id": attachment["id"],
            "size": "1536x1024",
        },
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    edited = json.loads(out)
    assert edited["status"] == "image_edited"
    assert (edited["width"], edited["height"]) == (1536, 1024)
    assert calls == [(FAKE_PNG, "photo.png", "image/png", "1536x1024")]


def test_image_edit_tool_rejects_bad_source(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    config = {"configurable": {"user_id": user["id"], "chat_id": chat["id"]}}
    monkeypatch.setattr(
        tools, "_openai_edit_image", lambda *a: (b"x", "image/png")
    )

    # No source at all.
    out = tools.image_edit.invoke({"prompt": "tweak"}, config=config)
    assert "which picture" in out

    # Unknown / another user's image id.
    out = tools.image_edit.invoke(
        {"prompt": "tweak", "image_id": "nope"}, config=config
    )
    assert "couldn't find that picture" in out

    # A document attachment isn't an image.
    doc = db.create_attachment(
        user["id"], chat["id"], "notes.txt", b"hi",
        kind="document", mime="text/plain", text="hi",
    )
    out = tools.image_edit.invoke(
        {"prompt": "tweak", "attachment_id": doc["id"]}, config=config
    )
    assert "couldn't find that picture" in out


def test_image_edit_tool_without_context(isolated_db):
    from agent import tools

    out = tools.image_edit.invoke(
        {"prompt": "tweak", "image_id": "x"}, config={"configurable": {}}
    )
    assert "isn't available" in out


def test_image_edit_schema_hides_injected_config():
    from agent import tools

    assert "config" not in tools.image_edit.args
    assert set(tools.image_edit.args) == {"prompt", "image_id", "attachment_id", "size"}


def test_image_edit_part_from_result():
    import app.main as main

    marker = json.dumps(
        {
            "status": "image_edited",
            "image_id": "img-2",
            "source_image_id": "img-1",
            "prompt": "add a hat",
            "width": 1024,
            "height": 1024,
        }
    )
    part = main._image_part_from_result("image_edit", marker)
    assert part == {
        "type": "image",
        "imageId": "img-2",
        "prompt": "add a hat",
        "width": 1024,
        "height": 1024,
    }
    assert main._image_part_from_result("web_search", marker) is None
    assert main._image_part_from_result("image_edit", "nope") is None
    # A result without a saved image id never becomes an image part.
    assert (
        main._image_part_from_result(
            "image_edit", json.dumps({"status": "image_edited"})
        )
        is None
    )


def test_attachment_blocks_include_image_id_pointer(isolated_db):
    import app.main as main

    user, chat = _user_and_chat()
    attachment = db.create_attachment(
        user["id"], chat["id"], "photo.png", FAKE_PNG,
        kind="image", mime="image/png",
    )
    blocks = main._attachment_blocks(
        [{"type": "attachment", "attachmentId": attachment["id"]}], user["id"]
    )
    assert blocks[0]["type"] == "image_url"
    assert blocks[1]["type"] == "text"
    assert attachment["id"] in blocks[1]["text"]
    assert "image_edit" in blocks[1]["text"]


def test_stream_stamps_each_thinking_block(signed_in, monkeypatch):
    """A thinking block keeps counting through tools and later reasoning.

    Each run of reasoning/tool parts is one block; the run is stamped with its
    duration when text follows (or the stream ends), so a block that spans a
    tool call reports one running total rather than stopping at the tool.
    """
    import app.main as main
    from types import SimpleNamespace

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()

    def reasoning(text):
        msg = SimpleNamespace(content="", additional_kwargs={"reasoning": text})
        return {
            "event": "on_chat_model_stream",
            "data": {"chunk": msg},
        }

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            yield {"event": "on_chat_model_start", "data": {}}
            yield reasoning("Think first. ")
            yield {
                "event": "on_tool_start",
                "run_id": "r1",
                "name": "web_search",
                "data": {"input": {"query": "london"}},
            }
            yield {
                "event": "on_tool_end",
                "run_id": "r1",
                "name": "web_search",
                "data": {"output": "rain"},
            }
            yield reasoning("Think again. ")
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="Let me check.")},
            }
            yield reasoning("Final thought. ")
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="Done.")},
            }

    monkeypatch.setattr(main, "GRAPH", FakeGraph())
    res = signed_in.post(
        "/api/v1/chat/stream",
        json={
            "chatId": chat["id"],
            "messages": [{"role": "user", "content": "hi"}],
            "generateTitle": False,
        },
    )
    assert res.status_code == 200

    parts = signed_in.get("/api/v1/chats").json()[0]["messages"][-1]["parts"]
    kinds = [p["type"] for p in parts]
    assert kinds == ["reasoning", "tool", "reasoning", "text", "reasoning", "text"]

    # First block (reasoning + tool + reasoning) shares one duration.
    first = parts[0]["seconds"]
    assert isinstance(first, float)
    assert parts[1]["seconds"] == first
    assert parts[2]["seconds"] == first
    assert first >= 0
    # Text never carries a duration.
    assert "seconds" not in parts[3]
    # Second block gets its own duration.
    assert isinstance(parts[4]["seconds"], float)
    assert "seconds" not in parts[5]
