import base64
import json

from app import db

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def _png(width: int, height: int) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height)).save(buffer, format="PNG")
    return buffer.getvalue()


def _user_and_chat():
    user = db.create_google_user("artist@example.com", "Artist", "google-sub-art")
    chat = db.create_chat(user["id"], title="Draw things")
    return user, chat


def test_generate_image_tool_saves_file_and_row(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    png = _png(640, 480)
    monkeypatch.setattr(
        tools,
        "_openai_generate_image",
        lambda prompt, size, transparent_background=False: (png, "image/png"),
    )

    out = tools.generate_image.invoke(
        {"prompt": "a cat in a tutu"},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    data = json.loads(out)
    assert data["status"] == "image_generated"
    assert data["prompt"] == "a cat in a tutu"
    assert data["size"] == "auto"
    assert data["width"] == 640
    assert data["height"] == 480

    row = db.get_image(user["id"], data["image_id"])
    assert row is not None
    assert row["chat_id"] == chat["id"]
    assert row["mime"] == "image/png"
    assert row["size_bytes"] == len(png)
    assert db.image_file_path(row["id"], row["mime"]).read_bytes() == png

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
    assert set(tools.generate_image.args) == {
        "prompt", "size", "transparent_background", "characters"
    }


def test_generate_image_tool_reports_api_failure(isolated_db, monkeypatch):
    import httpx

    from agent import tools

    user, chat = _user_and_chat()

    def boom(prompt, size, transparent_background=False):
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

    def fake_generate(prompt, size, transparent_background=False):
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

    # The multiplication sign is normalized; junk falls back to "auto".
    tools.generate_image.invoke(
        {"prompt": "a cat", "size": "1536×1024"}, config=config
    )
    fallback = json.loads(tools.generate_image.invoke(
        {"prompt": "a cat", "size": "huge"}, config=config
    ))
    assert seen == ["1536x1024", "1024x1536", "1536x1024", "auto"]
    assert fallback["size"] == "auto"


def test_generate_image_tool_requests_transparent_background(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    config = {"configurable": {"user_id": user["id"], "chat_id": chat["id"]}}
    seen = []

    def fake_generate(prompt, size, transparent_background=False):
        seen.append(transparent_background)
        return FAKE_PNG, "image/png"

    monkeypatch.setattr(tools, "_openai_generate_image", fake_generate)

    cut_out = json.loads(tools.generate_image.invoke(
        {"prompt": "a sticker cat", "transparent_background": True}, config=config
    ))
    assert cut_out["background"] == "transparent"
    assert seen == [True]

    opaque = json.loads(tools.generate_image.invoke(
        {"prompt": "a cat"}, config=config
    ))
    assert opaque["background"] == "auto"
    assert seen == [True, False]


def test_openai_image_calls_send_transparent_background(monkeypatch):
    from agent import tools

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(FAKE_PNG).decode()}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None, data=None, files=None):
            calls.append({"url": url, "json": json, "data": data, "files": files})
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)

    tools._openai_generate_image("a cat", "1024x1024", True)
    tools._openai_edit_image(
        "add a hat", [("in.png", FAKE_PNG, "image/png")], "1024x1024", True
    )
    tools._openai_generate_image("a cat", "1024x1024", False)

    assert calls[0]["json"]["background"] == "transparent"
    assert calls[1]["data"]["background"] == "transparent"
    assert calls[1]["files"]["image"][0] == "in.png"
    assert "background" not in calls[2]["json"]


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


def test_image_part_from_result_marks_transparent():
    import app.main as main

    marker = json.dumps(
        {
            "status": "image_generated",
            "image_id": "img-3",
            "prompt": "a sticker",
            "background": "transparent",
            "width": 1024,
            "height": 1024,
        }
    )
    part = main._image_part_from_result("generate_image", marker)
    assert part["transparent"] is True
    # An opaque/auto image never carries the marker.
    opaque = main._image_part_from_result(
        "generate_image",
        json.dumps({"status": "image_generated", "image_id": "img-4"}),
    )
    assert "transparent" not in opaque


def test_state_replays_image_ids_to_the_model(isolated_db):
    import app.main as main

    user, chat = _user_and_chat()
    image = db.create_image(user["id"], chat["id"], FAKE_PNG, prompt="a penguin")
    req = main.ChatRequest(
        chatId=chat["id"],
        messages=[
            {"role": "user", "content": "draw a penguin"},
            {
                "role": "assistant",
                "content": "Here you go!",
                "parts": [
                    {"type": "text", "text": "Here you go!"},
                    {
                        "type": "image",
                        "imageId": image["id"],
                        "prompt": "a penguin",
                    },
                ],
            },
            {"role": "user", "content": "remove the background"},
        ],
    )
    state = main._state(req, user)
    assistant = state["messages"][1]
    # The model can see the earlier picture's id and knows how to edit it.
    assert f"[Generated image (id={image['id']})]" in assistant.content
    # The user turns stay untouched.
    assert state["messages"][0].content == "draw a penguin"
    assert state["messages"][2].content == "remove the background"


def test_state_assistant_without_images_is_plain_text(isolated_db):
    import app.main as main

    user, chat = _user_and_chat()
    req = main.ChatRequest(
        chatId=chat["id"],
        messages=[
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "Hello!",
                "parts": [{"type": "text", "text": "Hello!"}],
            },
        ],
    )
    state = main._state(req, user)
    assert state["messages"][1].content == "Hello!"


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
    assert res.headers["cache-control"] == "private, max-age=5184000, immutable"

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

    def fake_edit(prompt, images, size, transparent_background=False):
        calls.append((prompt, images, size))
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
        (
            "give it a hat",
            [(f"{source['id']}.png", FAKE_PNG, "image/png")],
            "auto",
        )
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
        lambda prompt, images, size, transparent_background=False: (
            calls.append((images, size))
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
    assert calls == [([("photo.png", FAKE_PNG, "image/png")], "1536x1024")]


def test_image_edit_tool_requests_transparent_background(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    source = db.create_image(user["id"], chat["id"], FAKE_PNG, prompt="a cat")
    seen = []

    def fake_edit(prompt, images, size, transparent_background=False):
        seen.append(transparent_background)
        return b"edited", "image/png"

    monkeypatch.setattr(tools, "_openai_edit_image", fake_edit)

    cut_out = json.loads(tools.image_edit.invoke(
        {"prompt": "remove the background", "image_id": source["id"],
         "transparent_background": True},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    ))
    assert cut_out["background"] == "transparent"
    assert seen == [True]


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
    assert set(tools.image_edit.args) == {
        "prompt", "image_id", "attachment_id", "size", "transparent_background",
        "characters",
    }


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
    assert blocks[1]["text"] == f'[Attached image "photo.png" (id={attachment["id"]})]'


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


# ---------- character reference images ----------


def _character_with_ref(tmp_path, name, ref=True):
    from agent import lorebook

    character_id = name.lower().replace(" ", "-")
    ref_path = None
    if ref:
        ref_path = tmp_path / f"{character_id}.png"
        ref_path.write_bytes(FAKE_PNG)
    return lorebook.Character(
        id=character_id,
        name=name,
        aliases=(),
        blurb="A character for tests.",
        body="Body.",
        ref_path=ref_path,
    )


def test_generate_image_attaches_character_reference(isolated_db, monkeypatch, tmp_path):
    from agent import lorebook, tools

    user, chat = _user_and_chat()
    character = _character_with_ref(tmp_path, "Tralalero Tralala")
    monkeypatch.setattr(lorebook, "CHARACTERS", [character])
    monkeypatch.setattr(tools, "CHARACTER_REF_IMAGES", True)
    calls = []

    def fake_edit(prompt, images, size, transparent_background=False):
        calls.append((prompt, images, size))
        return b"drawn", "image/png"

    monkeypatch.setattr(tools, "_openai_edit_image", fake_edit)
    monkeypatch.setattr(
        tools,
        "_openai_generate_image",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should use edit")),
    )

    out = tools.generate_image.invoke(
        {"prompt": "at the beach", "characters": "Tralalero Tralala"},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    data = json.loads(out)
    assert data["character_refs"] == ["tralalero-tralala"]
    (prompt, images, size), = calls
    assert "official reference looks for Tralalero Tralala" in prompt
    assert prompt.endswith("at the beach")
    assert images == [("tralalero-tralala.png", FAKE_PNG, "image/png")]


def test_generate_image_skips_characters_without_references(
    isolated_db, monkeypatch, tmp_path
):
    from agent import lorebook, tools

    user, chat = _user_and_chat()
    character = _character_with_ref(tmp_path, "Tralalero Tralala", ref=False)
    monkeypatch.setattr(lorebook, "CHARACTERS", [character])
    monkeypatch.setattr(tools, "CHARACTER_REF_IMAGES", True)
    calls = []
    monkeypatch.setattr(
        tools,
        "_openai_generate_image",
        lambda prompt, size, transparent_background=False: (
            calls.append(prompt) or (b"drawn", "image/png")
        ),
    )
    monkeypatch.setattr(
        tools,
        "_openai_edit_image",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should generate")),
    )

    out = tools.generate_image.invoke(
        {"prompt": "at the beach", "characters": "Tralalero Tralala"},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    assert "character_refs" not in json.loads(out)
    assert calls == ["at the beach"]


def test_character_references_can_be_disabled(isolated_db, monkeypatch, tmp_path):
    from agent import lorebook, tools

    user, chat = _user_and_chat()
    character = _character_with_ref(tmp_path, "Tralalero Tralala")
    monkeypatch.setattr(lorebook, "CHARACTERS", [character])
    monkeypatch.setattr(tools, "CHARACTER_REF_IMAGES", False)
    monkeypatch.setattr(
        tools,
        "_openai_generate_image",
        lambda prompt, size, transparent_background=False: (FAKE_PNG, "image/png"),
    )
    monkeypatch.setattr(
        tools,
        "_openai_edit_image",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should generate")),
    )

    out = tools.generate_image.invoke(
        {"prompt": "at the beach", "characters": "Tralalero Tralala"},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    assert "character_refs" not in json.loads(out)


def test_character_references_are_capped(monkeypatch, tmp_path):
    from agent import lorebook, tools

    characters = [_character_with_ref(tmp_path, f"Char {i}") for i in range(1, 6)]
    monkeypatch.setattr(lorebook, "CHARACTERS", characters)
    monkeypatch.setattr(tools, "CHARACTER_REF_IMAGES", True)

    refs = tools._character_refs(", ".join(f"Char {i}" for i in range(1, 6)))
    assert len(refs) == tools.MAX_CHARACTER_REFS


def test_character_references_dedupe_by_identity(monkeypatch, tmp_path):
    from agent import lorebook, tools

    ref = tmp_path / "tralalero-tralala.png"
    ref.write_bytes(FAKE_PNG)
    character = lorebook.Character(
        id="tralalero-tralala",
        name="Tralalero Tralala",
        aliases=("Tralalero",),
        blurb="A character for tests.",
        body="Body.",
        ref_path=ref,
    )
    monkeypatch.setattr(lorebook, "CHARACTERS", [character])
    monkeypatch.setattr(tools, "CHARACTER_REF_IMAGES", True)

    assert len(tools._character_refs("Tralalero, Tralalero Tralala")) == 1


def test_character_references_skip_unreadable(monkeypatch, tmp_path):
    from agent import lorebook, tools

    character = lorebook.Character(
        id="ghost",
        name="Ghost",
        aliases=(),
        blurb="A character for tests.",
        body="Body.",
        ref_path=tmp_path / "missing.png",
    )
    monkeypatch.setattr(lorebook, "CHARACTERS", [character])
    monkeypatch.setattr(tools, "CHARACTER_REF_IMAGES", True)

    assert tools._character_refs("Ghost") == []


def test_image_edit_appends_character_reference(isolated_db, monkeypatch, tmp_path):
    from agent import lorebook, tools

    user, chat = _user_and_chat()
    source = db.create_image(user["id"], chat["id"], FAKE_PNG, prompt="a cat")
    character = _character_with_ref(tmp_path, "Tralalero Tralala")
    monkeypatch.setattr(lorebook, "CHARACTERS", [character])
    monkeypatch.setattr(tools, "CHARACTER_REF_IMAGES", True)
    calls = []
    monkeypatch.setattr(
        tools,
        "_openai_edit_image",
        lambda prompt, images, size, transparent_background=False: (
            calls.append((prompt, images)) or (b"edited", "image/png")
        ),
    )

    out = tools.image_edit.invoke(
        {
            "prompt": "add a hat",
            "image_id": source["id"],
            "characters": "Tralalero Tralala",
        },
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    data = json.loads(out)
    assert data["character_refs"] == ["tralalero-tralala"]
    (prompt, images), = calls
    assert prompt.startswith("The first image is the picture to edit;")
    assert prompt.endswith("add a hat")
    assert images == [
        (f"{source['id']}.png", FAKE_PNG, "image/png"),
        ("tralalero-tralala.png", FAKE_PNG, "image/png"),
    ]


def test_openai_edit_sends_image_array_for_multiple(monkeypatch):
    from agent import tools

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(FAKE_PNG).decode()}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None, data=None, files=None):
            calls.append({"url": url, "files": files})
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)

    tools._openai_edit_image(
        "draw them together",
        [
            ("a.png", FAKE_PNG, "image/png"),
            ("b.png", FAKE_PNG, "image/png"),
        ],
        "1024x1024",
    )

    assert calls[0]["files"] == [
        ("image[]", ("a.png", FAKE_PNG, "image/png")),
        ("image[]", ("b.png", FAKE_PNG, "image/png")),
    ]


def _assistant_with_ref(tmp_path):
    from agent import lorebook

    ref = tmp_path / "ballerina-cappuccina.png"
    ref.write_bytes(FAKE_PNG)
    return lorebook.Character(
        id=lorebook.ASSISTANT_ID,
        name="Ballerina Cappuccina",
        aliases=("Cappuccina", "yourself"),
        blurb="Cappuccina herself.",
        body="",
        ref_path=ref,
    )


def test_generate_image_attaches_assistant_reference(
    isolated_db, monkeypatch, tmp_path
):
    from agent import lorebook, tools

    user, chat = _user_and_chat()
    monkeypatch.setattr(
        lorebook, "reference_characters", lambda folder=None: [_assistant_with_ref(tmp_path)]
    )
    monkeypatch.setattr(tools, "CHARACTER_REF_IMAGES", True)
    calls = []
    monkeypatch.setattr(
        tools,
        "_openai_edit_image",
        lambda prompt, images, size, transparent_background=False: (
            calls.append(images) or (b"drawn", "image/png")
        ),
    )
    monkeypatch.setattr(
        tools,
        "_openai_generate_image",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should use edit")),
    )

    out = tools.generate_image.invoke(
        {"prompt": "a self portrait", "characters": "Ballerina Cappuccina"},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    assert json.loads(out)["character_refs"] == [lorebook.ASSISTANT_ID]
    assert calls == [[("ballerina-cappuccina.png", FAKE_PNG, "image/png")]]


def test_character_references_match_the_assistant_alias(monkeypatch, tmp_path):
    from agent import lorebook, tools

    monkeypatch.setattr(
        lorebook, "reference_characters", lambda folder=None: [_assistant_with_ref(tmp_path)]
    )
    monkeypatch.setattr(tools, "CHARACTER_REF_IMAGES", True)

    refs = tools._character_refs("yourself")
    assert [c.id for c, _ in refs] == [lorebook.ASSISTANT_ID]


# ---------- image_edit resolution ----------


def _capture_edit_size(monkeypatch):
    from agent import tools

    seen = []
    monkeypatch.setattr(
        tools,
        "_openai_edit_image",
        lambda prompt, images, size, transparent_background=False: (
            seen.append(size) or (b"edited", "image/png")
        ),
    )
    return seen


def test_image_size_defaults_to_auto(isolated_db):
    from agent import tools

    assert tools._image_size("") == "auto"
    assert tools._image_size("1536×1024") == "1536x1024"
    assert tools._image_size("huge") == "auto"


def test_image_edit_defaults_to_auto(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    config = {"configurable": {"user_id": user["id"], "chat_id": chat["id"]}}
    seen = _capture_edit_size(monkeypatch)

    for width, height in ((1536, 1024), (1024, 1536), (1024, 1024), (800, 600)):
        source = db.create_image(
            user["id"], chat["id"], FAKE_PNG, width=width, height=height
        )
        tools.image_edit.invoke(
            {"prompt": "tweak", "image_id": source["id"]}, config=config
        )

    assert seen == ["auto", "auto", "auto", "auto"]


def test_image_edit_attachment_defaults_to_auto(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    attachment = db.create_attachment(
        user["id"], chat["id"], "tall.png", _png(1024, 1536),
        kind="image", mime="image/png",
    )
    seen = _capture_edit_size(monkeypatch)

    tools.image_edit.invoke(
        {"prompt": "tweak", "attachment_id": attachment["id"]},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    assert seen == ["auto"]


def test_image_edit_explicit_size_overrides_the_source(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    source = db.create_image(
        user["id"], chat["id"], FAKE_PNG, width=1536, height=1024
    )
    seen = _capture_edit_size(monkeypatch)

    tools.image_edit.invoke(
        {"prompt": "tweak", "image_id": source["id"], "size": "1024x1024"},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    assert seen == ["1024x1024"]


# ---------- view_image ----------


def _view_call(image_id: str, call_id: str = "v1") -> dict:
    return {
        "name": "view_image",
        "args": {"image_id": image_id},
        "id": call_id,
        "type": "tool_call",
    }


def test_view_image_returns_picture_as_artifact(isolated_db):
    from agent import tools

    user, chat = _user_and_chat()
    image = db.create_image(user["id"], chat["id"], FAKE_PNG, prompt="a cat")

    result = tools.view_image.invoke(
        _view_call(image["id"]),
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    # The text result stays tiny; the bytes ride only in the artifact.
    assert "base64" not in result.content
    encoded = base64.b64encode(FAKE_PNG).decode("ascii")
    assert result.artifact == {
        "image_id": image["id"],
        "image_url": f"data:image/png;base64,{encoded}",
    }


def test_view_image_is_user_scoped(isolated_db):
    from agent import tools

    user, chat = _user_and_chat()
    image = db.create_image(user["id"], chat["id"], FAKE_PNG, prompt="a cat")

    result = tools.view_image.invoke(
        _view_call(image["id"]),
        config={"configurable": {"user_id": "someone-else", "chat_id": chat["id"]}},
    )
    assert "couldn't find" in result.content
    assert not result.artifact


def test_view_image_schema_hides_injected_config():
    from agent import tools

    assert set(tools.view_image.args) == {"image_id"}


def test_viewed_images_message_puts_picture_after_tool_batch():
    from langchain_core.messages import ToolMessage

    from agent.graph import is_viewed_image_message, viewed_images_message

    viewed = ToolMessage(
        content="Showing generated image img-1 below.",
        tool_call_id="v1",
        name="view_image",
        artifact={"image_id": "img-1", "image_url": "data:image/png;base64,AAAA"},
    )
    other = ToolMessage(content="ok", tool_call_id="t2", name="web_search")

    message = viewed_images_message([viewed, other])
    assert is_viewed_image_message(message)
    assert message.content[0] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAAA"},
    }
    assert "img-1" in message.content[1]["text"]

    # Nothing to show: no message at all.
    assert viewed_images_message([other]) is None
    failed = ToolMessage(content="couldn't find", tool_call_id="v2", name="view_image")
    assert viewed_images_message([failed]) is None


def test_viewed_image_message_does_not_start_a_new_run():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from agent.graph import _current_run, viewed_images_message

    user_turn = HumanMessage(content="change the hat")
    call = AIMessage(content="", tool_calls=[_view_call("img-1")])
    result = ToolMessage(
        content="Showing",
        tool_call_id="v1",
        name="view_image",
        artifact={"image_id": "img-1", "image_url": "data:image/png;base64,AAAA"},
    )
    injected = viewed_images_message([result])
    run = _current_run([user_turn, call, result, injected])
    assert run == [call, result, injected]


def test_final_extras_skips_viewed_image_message():
    import app.main as main
    from langchain_core.messages import AIMessage, ToolMessage

    from agent.graph import viewed_images_message

    result = ToolMessage(
        content="Showing generated image img-1 below.",
        tool_call_id="v1",
        name="view_image",
        artifact={"image_id": "img-1", "image_url": "data:image/png;base64,AAAA"},
    )
    messages = [
        AIMessage(content="", tool_calls=[_view_call("img-1")]),
        result,
        viewed_images_message([result]),
        AIMessage(content="It looks lovely."),
    ]
    _, _, parts = main._final_extras(messages)
    assert [p["type"] for p in parts] == ["tool", "text"]
    assert parts[1]["text"] == "It looks lovely."
    assert "base64" not in json.dumps(parts)


def test_state_keeps_viewed_image_in_later_turns(isolated_db):
    import app.main as main

    from agent.graph import is_viewed_image_message

    user, chat = _user_and_chat()
    image = db.create_image(user["id"], chat["id"], FAKE_PNG, prompt="a penguin")
    view_part = {
        "type": "tool",
        "id": "v1",
        "name": "view_image",
        "args": {"image_id": image["id"]},
        "result": "Showing",
        "ok": True,
    }
    req = main.ChatRequest(
        chatId=chat["id"],
        messages=[
            {"role": "user", "content": "how does it look?"},
            {
                "role": "assistant",
                "content": "Lovely!",
                # Viewed twice in one reply: shown once.
                "parts": [view_part, {**view_part, "id": "v2"}],
            },
            {"role": "user", "content": "what colour is the hat?"},
        ],
    )
    state = main._state(req, user)
    viewed = state["messages"][1]
    assert is_viewed_image_message(viewed)
    encoded = base64.b64encode(FAKE_PNG).decode("ascii")
    assert viewed.content[0]["image_url"]["url"] == f"data:image/png;base64,{encoded}"
    assert len(viewed.content) == 2
    assert state["messages"][2].content == "Lovely!"
    assert state["messages"][3].content == "what colour is the hat?"


def test_state_skips_failed_or_foreign_viewed_images(isolated_db):
    import app.main as main

    user, chat = _user_and_chat()
    other = db.create_google_user("other@example.com", "Other", "google-sub-other")
    other_chat = db.create_chat(other["id"], title="Theirs")
    theirs = db.create_image(other["id"], other_chat["id"], FAKE_PNG, prompt="x")
    mine = db.create_image(user["id"], chat["id"], FAKE_PNG, prompt="y")
    req = main.ChatRequest(
        chatId=chat["id"],
        messages=[
            {"role": "user", "content": "look"},
            {
                "role": "assistant",
                "content": "Hmm.",
                "parts": [
                    {"type": "tool", "name": "view_image", "args": {"image_id": theirs["id"]}, "ok": True},
                    {"type": "tool", "name": "view_image", "args": {"image_id": mine["id"]}, "ok": False},
                    {"type": "tool", "name": "view_image", "args": {"image_id": "missing"}, "ok": True},
                ],
            },
            {"role": "user", "content": "again"},
        ],
    )
    state = main._state(req, user)
    assert [m.content for m in state["messages"]] == ["look", "Hmm.", "again"]
