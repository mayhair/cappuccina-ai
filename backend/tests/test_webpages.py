import json

from app import db

PAGE = "<!doctype html><title>Hi</title><link rel='stylesheet' href='styles.css'>"
CSS = "body { background: pink; }"
JS = "console.log('hi')"


def _user_and_chat():
    user = db.create_google_user("builder@example.com", "Builder", "google-sub-build")
    chat = db.create_chat(user["id"], title="Build things")
    return user, chat


def _config(user, chat):
    return {"configurable": {"user_id": user["id"], "chat_id": chat["id"]}}


def _marker(env_id, path, files):
    return json.dumps(
        {
            "status": "webpage_file_written",
            "webpage_id": env_id,
            "path": path,
            "bytes": 10,
            "files": files,
        }
    )


# ---------- write_webpage_file ----------


def test_write_webpage_file_creates_env_and_upserts(isolated_db):
    from agent import tools

    user, chat = _user_and_chat()
    config = _config(user, chat)

    out = tools.write_webpage_file.invoke(
        {"path": "index.html", "content": PAGE}, config=config
    )
    data = json.loads(out)
    assert data["status"] == "webpage_file_written"
    assert data["path"] == "index.html"
    assert data["files"] == ["index.html"]
    assert data["bytes"] == len(PAGE.encode())
    env_id = data["webpage_id"]

    # Writing another file keeps the same environment.
    second = json.loads(
        tools.write_webpage_file.invoke(
            {"path": "styles.css", "content": CSS}, config=config
        )
    )
    assert second["webpage_id"] == env_id
    assert second["files"] == ["index.html", "styles.css"]

    # Rewriting a file updates it in place (no duplicate).
    again = json.loads(
        tools.write_webpage_file.invoke(
            {"path": "index.html", "content": "<h1>changed</h1>"}, config=config
        )
    )
    assert again["webpage_id"] == env_id
    assert again["files"] == ["index.html", "styles.css"]

    env = db.get_webpage_env(user["id"], chat["id"])
    assert env is not None
    assert env["id"] == env_id
    assert env["files"] == {"index.html": "<h1>changed</h1>", "styles.css": CSS}


def test_write_webpage_file_without_context(isolated_db):
    from agent import tools

    out = tools.write_webpage_file.invoke(
        {"path": "index.html", "content": PAGE}, config={"configurable": {}}
    )
    assert "isn't available" in out


def test_webpage_tools_schema_hides_injected_config():
    from agent import tools

    assert "config" not in tools.write_webpage_file.args
    assert set(tools.write_webpage_file.args) == {"path", "content"}
    assert "config" not in tools.read_webpage_file.args
    assert set(tools.read_webpage_file.args) == {"path"}


def test_write_webpage_file_rejects_bad_paths(isolated_db):
    from agent import tools

    user, chat = _user_and_chat()
    config = _config(user, chat)

    for bad in (
        "",
        "../evil.html",
        "a/../../evil.html",
        "/etc/passwd",
        "C:/windows.html",
        "index.exe",
        "noextension",
        ".hidden.html",
        "a/b/c/d/e.html",
        "bad name.html",
    ):
        out = tools.write_webpage_file.invoke(
            {"path": bad, "content": PAGE}, config=config
        )
        assert "isn't allowed" in out, bad
    # Nothing was stored.
    assert db.get_webpage_env(user["id"], chat["id"]) is None


def test_write_webpage_file_rejects_empty_and_oversized(isolated_db):
    from agent import tools

    user, chat = _user_and_chat()
    config = _config(user, chat)

    assert "empty" in tools.write_webpage_file.invoke(
        {"path": "index.html", "content": ""}, config=config
    )

    too_big = "x" * (tools.WEBPAGE_MAX_FILE_BYTES + 1)
    assert "too big" in tools.write_webpage_file.invoke(
        {"path": "index.html", "content": too_big}, config=config
    )

    # A file just under the total cap fits; one more pushes over.
    chunk = "y" * (tools.WEBPAGE_MAX_TOTAL_BYTES // 3)
    assert "webpage_file_written" in tools.write_webpage_file.invoke(
        {"path": "a.css", "content": chunk}, config=config
    )
    assert "webpage_file_written" in tools.write_webpage_file.invoke(
        {"path": "b.css", "content": chunk}, config=config
    )
    assert "webpage_file_written" in tools.write_webpage_file.invoke(
        {"path": "c.css", "content": chunk}, config=config
    )
    over = tools.write_webpage_file.invoke(
        {"path": "d.css", "content": chunk + "z"}, config=config
    )
    assert "too big" in over


# ---------- read_webpage_file ----------


def test_read_webpage_file_round_trip(isolated_db):
    from agent import tools

    user, chat = _user_and_chat()
    config = _config(user, chat)

    assert "no webpage yet" in tools.read_webpage_file.invoke(
        {"path": "index.html"}, config=config
    )

    tools.write_webpage_file.invoke({"path": "index.html", "content": PAGE}, config=config)
    tools.write_webpage_file.invoke({"path": "script.js", "content": JS}, config=config)

    assert tools.read_webpage_file.invoke({"path": "script.js"}, config=config) == JS

    missing = tools.read_webpage_file.invoke({"path": "style.css"}, config=config)
    assert "No file named that" in missing
    assert "index.html" in missing and "script.js" in missing

    bad = tools.read_webpage_file.invoke({"path": "nope.exe"}, config=config)
    assert "No file named that" in bad


def test_read_webpage_file_without_context(isolated_db):
    from agent import tools

    out = tools.read_webpage_file.invoke(
        {"path": "index.html"}, config={"configurable": {}}
    )
    assert "isn't available" in out


# ---------- isolation ----------


def test_webpage_env_is_user_scoped(isolated_db):
    from agent import tools

    user, chat = _user_and_chat()
    stranger = db.create_google_user("nosy@example.com", "Nosy", "google-sub-nosy")

    tools.write_webpage_file.invoke(
        {"path": "index.html", "content": PAGE}, config=_config(user, chat)
    )

    assert db.get_webpage_env(stranger["id"], chat["id"]) is None
    # A stranger can't read the page even with the chat id.
    out = tools.read_webpage_file.invoke(
        {"path": "index.html"},
        config={"configurable": {"user_id": stranger["id"], "chat_id": chat["id"]}},
    )
    assert "no webpage yet" in out


# ---------- parts, redaction and replay ----------


def test_webpage_part_from_result():
    import app.main as main

    part = main._webpage_part_from_result(
        _marker("env-1", "index.html", ["index.html", "styles.css"])
    )
    assert part == {
        "type": "webpage",
        "webpageId": "env-1",
        "path": "index.html",
        "files": ["index.html", "styles.css"],
    }
    assert main._webpage_part_from_result("nope") is None
    assert main._webpage_part_from_result("") is None
    assert main._webpage_part_from_result(json.dumps({"status": "other"})) is None
    assert (
        main._webpage_part_from_result(
            json.dumps({"status": "webpage_file_written", "webpage_id": "x"})
        )
        is None
    )


def test_merge_webpage_part_folds_one_turn_into_one_card():
    import app.main as main

    parts = []
    first = main._webpage_part_from_result(_marker("env-1", "index.html", ["index.html"]))
    second = main._webpage_part_from_result(
        _marker("env-1", "styles.css", ["index.html", "styles.css"])
    )
    main._merge_webpage_part(parts, dict(first))
    main._merge_webpage_part(parts, dict(second))
    assert len(parts) == 1
    assert parts[0]["files"] == ["index.html", "styles.css"]
    assert parts[0]["path"] == "styles.css"

    # A different environment starts its own part.
    other = main._webpage_part_from_result(_marker("env-2", "page.html", ["page.html"]))
    main._merge_webpage_part(parts, other)
    assert [p["webpageId"] for p in parts] == ["env-1", "env-2"]


def test_webpage_tool_args_strip_content():
    import app.main as main

    redacted = main._webpage_tool_args(
        "write_webpage_file", {"path": "index.html", "content": PAGE}
    )
    assert redacted == {"path": "index.html", "bytes": len(PAGE.encode())}
    # Other tools pass through untouched.
    assert main._webpage_tool_args("web_search", {"query": "hi"}) == {"query": "hi"}


def test_state_replays_webpage_files_to_the_model(isolated_db):
    import app.main as main

    user, chat = _user_and_chat()
    req = main.ChatRequest(
        chatId=chat["id"],
        messages=[
            {"role": "user", "content": "make me a page"},
            {
                "role": "assistant",
                "content": "All done!",
                "parts": [
                    {"type": "text", "text": "All done!"},
                    {
                        "type": "webpage",
                        "webpageId": "env-1",
                        "path": "index.html",
                        "files": ["index.html", "styles.css"],
                    },
                ],
            },
            {"role": "user", "content": "make the background blue"},
        ],
    )
    state = main._state(req, user)
    assistant = state["messages"][1]
    assert "[Webpage (files: index.html, styles.css)]" in assistant.content


def test_final_extras_appends_webpage_part_and_redacts_args():
    import app.main as main
    from langchain_core.messages import AIMessage, ToolMessage

    marker = _marker("env-9", "index.html", ["index.html"])
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "t1",
                    "name": "write_webpage_file",
                    "args": {"path": "index.html", "content": PAGE},
                }
            ],
        ),
        ToolMessage(content=marker, tool_call_id="t1"),
        AIMessage(content="Here it is!"),
    ]
    reasoning, tool_calls, parts = main._final_extras(messages)
    assert [p["type"] for p in parts] == ["tool", "webpage", "text"]
    assert parts[1]["webpageId"] == "env-9"
    assert parts[1]["files"] == ["index.html"]
    # Stored args keep the path and size but never the file text.
    assert parts[0]["args"] == {"path": "index.html", "bytes": len(PAGE.encode())}
    assert "content" not in parts[0]["args"]
    assert tool_calls[0].args == {"path": "index.html", "bytes": len(PAGE.encode())}


# ---------- API ----------


def test_get_webpage_round_trip(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None

    empty = signed_in.get(f"/api/v1/webpages/{chat['id']}")
    assert empty.status_code == 200
    assert empty.json() == {
        "webpageId": None,
        "files": {},
        "updatedAt": None,
        "activeVersionId": None,
    }

    env = db.put_webpage_file(user["id"], chat["id"], "index.html", PAGE)
    db.put_webpage_file(user["id"], chat["id"], "styles.css", CSS)

    res = signed_in.get(f"/api/v1/webpages/{chat['id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["webpageId"] == env["id"]
    assert body["files"] == {"index.html": PAGE, "styles.css": CSS}
    assert body["updatedAt"]


def test_get_webpage_unknown_chat_is_404(signed_in):
    assert signed_in.get("/api/v1/webpages/nope").status_code == 404


def test_get_webpage_requires_login(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    assert signed_in.post("/api/v1/auth/logout").json() == {"ok": True}
    assert signed_in.get(f"/api/v1/webpages/{chat['id']}").status_code == 401


def test_get_webpage_is_user_scoped(signed_in):
    stranger = db.create_google_user("stranger2@example.com", "Stranger", "sub-w")
    chat = db.create_chat(stranger["id"], title="Theirs")
    db.put_webpage_file(stranger["id"], chat["id"], "index.html", PAGE)

    assert signed_in.get(f"/api/v1/webpages/{chat['id']}").status_code == 404


def test_chat_delete_removes_webpage_env(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    db.put_webpage_file(user["id"], chat["id"], "index.html", PAGE)

    assert signed_in.delete(f"/api/v1/chats/{chat['id']}").json() == {"ok": True}
    assert db.get_webpage_env(user["id"], chat["id"]) is None


# ---------- streaming ----------


def _sse_events(text):
    events = {}
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if event:
            events.setdefault(event, []).append(json.loads(data) if data else {})
    return events


def test_stream_emits_webpage_event_and_persists_part(signed_in, monkeypatch):
    import app.main as main
    from types import SimpleNamespace

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    env = db.put_webpage_file(user["id"], chat["id"], "index.html", PAGE)
    marker = _marker(env["id"], "index.html", ["index.html"])

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            assert config["configurable"]["chat_id"] == chat["id"]
            yield {"event": "on_chat_model_start", "data": {}}
            yield {
                "event": "on_tool_start",
                "run_id": "r1",
                "name": "write_webpage_file",
                "data": {"input": {"path": "index.html", "content": PAGE}},
            }
            yield {
                "event": "on_tool_end",
                "run_id": "r1",
                "name": "write_webpage_file",
                "data": {"output": marker},
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="Here it is!")},
            }

    monkeypatch.setattr(main, "GRAPH", FakeGraph())
    res = signed_in.post(
        "/api/v1/chat/stream",
        json={
            "chatId": chat["id"],
            "messages": [{"role": "user", "content": "make me a page"}],
            "generateTitle": False,
        },
    )
    assert res.status_code == 200
    events = _sse_events(res.text)
    # The live event carries the file text so the canvas can render it.
    assert events["webpage"][0]["content"] == PAGE
    assert events["webpage"][0]["path"] == "index.html"
    assert events["webpage"][0]["webpageId"] == env["id"]
    # The live tool call shows only the path and size, never the file text.
    assert events["tool_call"][0]["args"] == {
        "path": "index.html",
        "bytes": len(PAGE.encode()),
    }

    # The stored part survives a reload, and its tool args hold no file text.
    stored = signed_in.get("/api/v1/chats").json()
    assistant = stored[0]["messages"][-1]
    webpage_parts = [p for p in assistant["parts"] if p.get("type") == "webpage"]
    assert len(webpage_parts) == 1
    assert webpage_parts[0]["webpageId"] == env["id"]
    assert webpage_parts[0]["files"] == ["index.html"]
    tool_parts = [p for p in assistant["parts"] if p.get("type") == "tool"]
    assert tool_parts[0]["args"] == {"path": "index.html", "bytes": len(PAGE.encode())}


def test_nonstream_chat_persists_webpage_part(signed_in, monkeypatch):
    import app.main as main
    from langchain_core.messages import AIMessage, ToolMessage

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    env = db.put_webpage_file(user["id"], chat["id"], "index.html", PAGE)
    marker = _marker(env["id"], "index.html", ["index.html"])

    class FakeGraph:
        async def ainvoke(self, state, config=None):
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "t1",
                                "name": "write_webpage_file",
                                "args": {"path": "index.html", "content": PAGE},
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
            "messages": [{"role": "user", "content": "make me a page"}],
        },
    )
    assert res.status_code == 200

    parts = signed_in.get("/api/v1/chats").json()[0]["messages"][-1]["parts"]
    webpage_parts = [p for p in parts if p.get("type") == "webpage"]
    assert webpage_parts[0]["webpageId"] == env["id"]
    tool_parts = [p for p in parts if p.get("type") == "tool"]
    assert "content" not in tool_parts[0]["args"]
