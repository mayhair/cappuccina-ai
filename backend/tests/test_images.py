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
        tools, "_openai_generate_image", lambda prompt: (FAKE_PNG, "image/png")
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
    assert set(tools.generate_image.args) == {"prompt"}


def test_generate_image_tool_reports_api_failure(isolated_db, monkeypatch):
    import httpx

    from agent import tools

    user, chat = _user_and_chat()

    def boom(prompt):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(tools, "_openai_generate_image", boom)
    out = tools.generate_image.invoke(
        {"prompt": "a cat"},
        config={"configurable": {"user_id": user["id"], "chat_id": chat["id"]}},
    )
    assert "Couldn't draw that" in out


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
    assert [p["type"] for p in parts] == ["tool", "image"]
    assert parts[1]["imageId"] == "img-9"
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
