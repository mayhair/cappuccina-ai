"""Website workspaces: the write/read/shell tools and the canvas plumbing.

Everything here runs without a container: a workspace is a folder plus a host
git repo, so only test_sitebox.py talks to Podman.
"""

import json

from app import db, sitebox

PAGE = "<!doctype html><title>Hi</title><link rel='stylesheet' href='styles.css'>"
CSS = "body { background: pink; }"
JS = "console.log('hi')"
# The NUL byte is what makes git (and read_webpage_file) treat this as binary.
PNG = b"\x89PNG\r\n\x1a\n\x00\x00" + b"p" * 900


def _user_and_chat(email="builder@example.com", sub="google-sub-build"):
    user = db.create_google_user(email, "Builder", sub)
    chat = db.create_chat(user["id"], title="Build things")
    return user, chat


def _config(user, chat, reply_id=None):
    configurable = {"user_id": user["id"], "chat_id": chat["id"]}
    if reply_id is not None:
        configurable["reply_id"] = reply_id
    return {"configurable": configurable}


def _site(user, chat):
    site = db.get_webpage_site(user["id"], chat["id"])
    assert site is not None
    return site


def _workspace(user, chat):
    return sitebox.workspace_dir(_site(user, chat)["id"])


def _write(user, chat, path, content, reply_id=None):
    from agent import tools

    return tools.write_webpage_file.invoke(
        {"path": path, "content": content}, config=_config(user, chat, reply_id)
    )


def _read(user, chat, path):
    from agent import tools

    return tools.read_webpage_file.invoke({"path": path}, config=_config(user, chat))


def _marker(webpage_id, path, files):
    return json.dumps(
        {
            "status": "webpage_file_written",
            "webpage_id": webpage_id,
            "version_id": None,
            "path": path,
            "bytes": 10,
            "files": files,
        }
    )


# ---------- write_webpage_file ----------


def test_first_write_creates_the_site_and_later_writes_keep_it(isolated_db):
    user, chat = _user_and_chat()

    data = json.loads(_write(user, chat, "index.html", PAGE))
    assert data["status"] == "webpage_file_written"
    assert data["version_id"] is None
    assert data["path"] == "index.html"
    assert data["bytes"] == len(PAGE.encode())
    assert data["files"] == ["index.html"]
    site_id = data["webpage_id"]
    assert _site(user, chat)["id"] == site_id

    second = json.loads(_write(user, chat, "styles.css", CSS))
    assert second["webpage_id"] == site_id
    assert second["files"] == ["index.html", "styles.css"]

    # Rewriting a file replaces its bytes and adds no second entry.
    again = json.loads(_write(user, chat, "index.html", "<h1>changed</h1>"))
    assert again["webpage_id"] == site_id
    assert again["files"] == ["index.html", "styles.css"]
    assert (_workspace(user, chat) / "index.html").read_text(encoding="utf-8") == (
        "<h1>changed</h1>"
    )


def test_subfolders_are_written_into_the_workspace(isolated_db):
    user, chat = _user_and_chat()

    data = json.loads(_write(user, chat, "src/pages/about.astro", "<h1>About</h1>"))
    assert data["path"] == "src/pages/about.astro"
    assert data["files"] == ["src/pages/about.astro"]
    assert (_workspace(user, chat) / "src" / "pages" / "about.astro").read_text(
        encoding="utf-8"
    ) == "<h1>About</h1>"


def test_write_without_context(isolated_db):
    from agent import tools

    out = tools.write_webpage_file.invoke(
        {"path": "index.html", "content": PAGE}, config={"configurable": {}}
    )
    assert "isn't available" in out


def test_webpage_tools_schema_hides_injected_config():
    from agent import tools

    assert "config" not in tools.write_webpage_file.args
    assert set(tools.write_webpage_file.args) == {"path", "content"}
    assert set(tools.read_webpage_file.args) == {"path"}
    assert set(tools.add_webpage_asset.args) == {"path", "image_id", "attachment_id"}
    # The container shell is the one tool with a timeout; config stays hidden.
    assert set(tools.webpage_shell.args) == {"command", "timeout"}
    assert "config" not in tools.webpage_shell.args


def test_write_rejects_names_that_escape_or_break_the_rules(isolated_db):
    user, chat = _user_and_chat()

    for bad in (
        "",
        "../evil.html",
        "a/../../evil.html",
        "/etc/passwd",
        "C:/windows.html",
        "bad name.html",
        ".git/config",
        "a/b/c/d/e/f/g/h/i/j/k",  # eleven folders deep
    ):
        out = _write(user, chat, bad, PAGE)
        assert "isn't allowed" in out, bad
    # Nothing but the seeded .gitignore landed on disk.
    workspace = _workspace(user, chat)
    assert sorted(p.name for p in workspace.iterdir()) == [".git", ".gitignore"]


def test_write_enforces_the_byte_limits(isolated_db):
    user, chat = _user_and_chat()

    assert "empty" in _write(user, chat, "index.html", "")
    assert "too big" in _write(user, chat, "big.txt", "x" * 1_000_001)

    # Exactly 1 MB fits.
    data = json.loads(_write(user, chat, "big.txt", "x" * 1_000_000))
    assert data["bytes"] == 1_000_000
    assert (_workspace(user, chat) / "big.txt").stat().st_size == 1_000_000


# ---------- read_webpage_file ----------


def test_read_without_a_site_explains_how_to_start(isolated_db):
    user, chat = _user_and_chat()

    out = _read(user, chat, "index.html")
    assert "There is no website in this chat yet" in out
    assert "write_webpage_file" in out


def test_read_round_trip_listing_and_binary(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", PAGE)
    _write(user, chat, "script.js", JS)

    assert _read(user, chat, "script.js") == JS

    missing = _read(user, chat, "style.css")
    assert "No file named that" in missing
    assert "index.html" in missing and "script.js" in missing

    # A name the write rules reject reads as a miss with the same listing.
    invalid = _read(user, chat, "nope.exe")
    assert "No file named that" in invalid

    # A binary file written straight into the workspace is described, not dumped.
    (_workspace(user, chat) / "logo.png").write_bytes(PNG)
    described = _read(user, chat, "logo.png")
    assert "logo.png is a picture in the site folder" in described
    assert "image/png" in described
    assert f"{len(PNG)} bytes" in described


# ---------- webpage_shell ----------


def test_shell_reports_an_unreachable_engine_verbatim(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    _write(user, chat, "index.html", PAGE)

    def unavailable(site, command, timeout):
        raise sitebox.SiteboxUnavailable(sitebox.UNAVAILABLE_MESSAGE)

    monkeypatch.setattr(tools.sitebox, "run_command", unavailable)
    out = tools.webpage_shell.invoke({"command": "echo hi"}, config=_config(user, chat))
    assert out == sitebox.UNAVAILABLE_MESSAGE

    # Any other runtime failure carries its own reason: a container that cannot
    # be built/created/started must show the CLI's words, not the generic line.
    reason = (
        "Could not build the website workspace image: "
        "mkdir /run/user/0/libpod: permission denied"
    )

    def broken(site, command, timeout):
        raise sitebox.SiteboxUnavailable(reason)

    monkeypatch.setattr(tools.sitebox, "run_command", broken)
    out = tools.webpage_shell.invoke({"command": "echo hi"}, config=_config(user, chat))
    assert out == reason


def test_shell_clamps_the_timeout_and_shapes_its_result(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    seen = []

    def fake(site, command, timeout):
        seen.append(timeout)
        return 0, "hi\n"

    monkeypatch.setattr(tools.sitebox, "run_command", fake)

    data = json.loads(
        tools.webpage_shell.invoke(
            {"command": "echo hi", "timeout": 9000}, config=_config(user, chat)
        )
    )
    assert seen == [600]
    assert data["status"] == "webpage_command_ran"
    assert data.get("version_id") is None
    assert data["webpage_id"] == _site(user, chat)["id"]
    assert data["exit_code"] == 0
    assert data["output"] == "hi\n"

    tools.webpage_shell.invoke({"command": "x", "timeout": -5}, config=_config(user, chat))
    tools.webpage_shell.invoke({"command": "x", "timeout": 600}, config=_config(user, chat))
    assert seen == [600, 1, 600]


def test_shell_without_context(isolated_db):
    from agent import tools

    out = tools.webpage_shell.invoke({"command": "ls"}, config={"configurable": {}})
    assert "isn't available" in out


# ---------- scoping ----------


def test_workspace_is_user_scoped(isolated_db):
    user, chat = _user_and_chat()
    stranger = db.create_google_user("nosy@example.com", "Nosy", "google-sub-nosy")
    _write(user, chat, "index.html", PAGE)

    assert db.get_webpage_site(stranger["id"], chat["id"]) is None
    out = _read(stranger, chat, "index.html")
    assert "There is no website in this chat yet" in out


# ---------- parts, redaction and replay ----------


def test_webpage_part_from_result():
    import app.main as main

    part = main._webpage_part_from_result(
        _marker("site-1", "index.html", ["index.html", "styles.css"])
    )
    assert part == {
        "type": "webpage",
        "webpageId": "site-1",
        "path": "index.html",
        "files": ["index.html", "styles.css"],
    }

    # The iteration id comes from the run's finalize step, not the tool result.
    assert main._webpage_result(
        _marker("site-1", "index.html", ["index.html", "styles.css"])
    ) == (part, None)

    # An asset or a shell command marks the site without a path of its own.
    shell = json.dumps(
        {
            "status": "webpage_command_ran",
            "webpage_id": "site-1",
            "exit_code": 0,
            "output": "hi\n",
            "files": ["index.html"],
        }
    )
    assert main._webpage_part_from_result(shell) == {
        "type": "webpage",
        "webpageId": "site-1",
        "files": ["index.html"],
    }

    assert main._webpage_part_from_result("nope") is None
    assert main._webpage_part_from_result("") is None
    assert main._webpage_part_from_result(json.dumps({"status": "other"})) is None
    assert (
        main._webpage_part_from_result(
            json.dumps({"status": "webpage_file_written", "webpage_id": "x"})
        )
        is not None
    )


def test_merge_webpage_part_folds_one_turn_into_one_card():
    import app.main as main

    parts = []
    first = main._webpage_part_from_result(_marker("site-1", "index.html", ["index.html"]))
    second = main._webpage_part_from_result(
        _marker("site-1", "styles.css", ["index.html", "styles.css"])
    )
    main._merge_webpage_part(parts, dict(first))
    main._merge_webpage_part(parts, dict(second))
    assert len(parts) == 1
    assert parts[0]["files"] == ["index.html", "styles.css"]
    assert parts[0]["path"] == "styles.css"

    # A different site starts its own part.
    other = main._webpage_part_from_result(_marker("site-2", "page.html", ["page.html"]))
    main._merge_webpage_part(parts, other)
    assert [p["webpageId"] for p in parts] == ["site-1", "site-2"]


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
                        "webpageId": "site-1",
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

    marker = _marker("site-9", "index.html", ["index.html"])
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
    assert parts[1]["webpageId"] == "site-9"
    assert parts[1]["files"] == ["index.html"]
    # Stored args keep the path and size but never the file text.
    assert parts[0]["args"] == {"path": "index.html", "bytes": len(PAGE.encode())}
    assert "content" not in parts[0]["args"]
    assert tool_calls[0].args == {"path": "index.html", "bytes": len(PAGE.encode())}


# ---------- API ----------


def test_get_webpage_round_trip(signed_in):
    from agent import tools

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None

    empty = signed_in.get(f"/api/v1/webpages/{chat['id']}")
    assert empty.status_code == 200
    assert empty.json() == {
        "webpageId": None,
        "files": [],
        "updatedAt": None,
        "activeVersionId": None,
        "previewUrl": None,
        "previewBase": None,
        "entry": "index.html",
        "serveRoot": "",
        "hasBuild": False,
        "lastBuild": None,
    }

    config = _config(user, chat, reply_id="reply-1")
    tools.write_webpage_file.invoke({"path": "index.html", "content": PAGE}, config=config)
    tools.write_webpage_file.invoke({"path": "styles.css", "content": CSS}, config=config)

    body = signed_in.get(f"/api/v1/webpages/{chat['id']}").json()
    site = db.get_webpage_site(user["id"], chat["id"])
    assert body["webpageId"] == site["id"]
    assert body["files"] == ["index.html", "styles.css"]
    assert body["entry"] == "index.html"
    assert body["serveRoot"] == ""
    assert body["hasBuild"] is False
    assert body["lastBuild"] is None
    assert body["updatedAt"]
    # The preview URL is the capability URL the canvas iframe loads.
    assert body["previewBase"] == (
        f"/api/v1/webpages/{chat['id']}/preview/{site['preview_key']}/"
    )
    assert body["previewUrl"] == body["previewBase"] + "index.html"
    # No file text ever rides in this payload.
    assert PAGE not in json.dumps(body)


def test_get_webpage_unknown_chat_is_404(signed_in):
    assert signed_in.get("/api/v1/webpages/nope").status_code == 404


def test_get_webpage_requires_login(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    assert signed_in.post("/api/v1/auth/logout").json() == {"ok": True}
    assert signed_in.get(f"/api/v1/webpages/{chat['id']}").status_code == 401


def test_get_webpage_is_user_scoped(signed_in):
    stranger = db.create_google_user("stranger2@example.com", "Stranger", "sub-w")
    chat = db.create_chat(stranger["id"], title="Theirs")
    db.create_webpage_site(stranger["id"], chat["id"], "their-key")

    assert signed_in.get(f"/api/v1/webpages/{chat['id']}").status_code == 404


def test_download_zips_the_raw_site(signed_in):
    import io
    import zipfile

    from agent import tools

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    assert signed_in.get(f"/api/v1/webpages/{chat['id']}/download").status_code == 404

    config = _config(user, chat)
    tools.write_webpage_file.invoke({"path": "index.html", "content": PAGE}, config=config)
    tools.write_webpage_file.invoke({"path": "styles.css", "content": CSS}, config=config)
    workspace = _workspace(user, chat)
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "dep.js").write_text("x")

    res = signed_in.get(f"/api/v1/webpages/{chat['id']}/download")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert 'filename="' in res.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(res.content))
    assert sorted(archive.namelist()) == ["index.html", "styles.css"]
    assert archive.read("index.html").decode() == PAGE


def test_download_is_user_scoped_and_needs_login(signed_in):
    stranger = db.create_google_user("stranger3@example.com", "Stranger", "sub-dl")
    chat = db.create_chat(stranger["id"], title="Theirs")
    db.create_webpage_site(stranger["id"], chat["id"], "their-key")
    assert signed_in.get(f"/api/v1/webpages/{chat['id']}/download").status_code == 404

    mine = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    signed_in.post("/api/v1/auth/logout")
    assert signed_in.get(f"/api/v1/webpages/{mine['id']}/download").status_code == 401


def test_chat_delete_removes_the_site_row(signed_in):
    from agent import tools

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    tools.write_webpage_file.invoke(
        {"path": "index.html", "content": PAGE}, config=_config(user, chat)
    )

    assert signed_in.delete(f"/api/v1/chats/{chat['id']}").json() == {"ok": True}
    assert db.get_webpage_site(user["id"], chat["id"]) is None


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


def test_stream_emits_webpage_events_without_file_text(signed_in, monkeypatch):
    import app.main as main
    from types import SimpleNamespace

    from agent import tools

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    user = db.get_user_by_email("kid@example.com")
    assert user is not None

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            assert config["configurable"]["chat_id"] == chat["id"]
            marker = tools.write_webpage_file.invoke(
                {"path": "index.html", "content": PAGE}, config=config
            )
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
            "replyId": "reply-stream",
            "generateTitle": False,
        },
    )
    assert res.status_code == 200
    events = _sse_events(res.text)

    # The live event only points the canvas at the preview; the text stays put.
    live, final = events["webpage"]
    assert "content" not in live and "path" not in live
    assert live["versionId"] is None
    assert live["build"] is None
    assert live["files"] == ["index.html"]
    assert live["previewBase"].endswith("/")
    assert live["previewUrl"] == live["previewBase"] + "index.html"
    # The run's finalize step announces the iteration it committed.
    site = db.get_webpage_site(user["id"], chat["id"])
    assert final["versionId"] == site["active_commit"]
    assert final["build"] is None

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
    assert webpage_parts[0]["webpageId"] == site["id"]
    assert webpage_parts[0]["files"] == ["index.html"]
    tool_parts = [p for p in assistant["parts"] if p.get("type") == "tool"]
    assert tool_parts[0]["args"] == {"path": "index.html", "bytes": len(PAGE.encode())}


def test_stream_shows_tool_call_before_arguments_finish(signed_in, monkeypatch):
    """The card is announced while the model streams the tool arguments.

    A webpage write carries the whole page in its arguments, so waiting for
    on_tool_start would hide the call until generation is nearly done.
    """
    import app.main as main
    from types import SimpleNamespace

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    marker = _marker("site-1", "index.html", ["index.html"])

    def chunk(name, args, call_id):
        return SimpleNamespace(
            content="",
            additional_kwargs={},
            tool_call_chunks=[
                {
                    "name": name,
                    "args": args,
                    "id": call_id,
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ],
        )

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            yield {"event": "on_chat_model_start", "data": {}}
            # The model announces the tool, then streams the page content.
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": chunk("write_webpage_file", "", "call_1")},
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {
                    "chunk": chunk(None, '{"path": "index.html", "content": "', None)
                },
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": chunk(None, PAGE + '"}', None)},
            }
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
    # Announced before the arguments finished, then again with the real args.
    assert len(events["tool_call"]) == 2
    assert events["tool_call"][0]["id"] == "call_1"
    assert events["tool_call"][0]["display"] == "Building Webpage"
    assert events["tool_call"][1]["id"] == "call_1"
    assert events["tool_call"][1]["args"] == {
        "path": "index.html",
        "bytes": len(PAGE.encode()),
    }
    # The result lands on the same card.
    assert events["tool_result"][0]["id"] == "call_1"

    stored = signed_in.get("/api/v1/chats").json()
    assistant = stored[0]["messages"][-1]
    tool_parts = [p for p in assistant["parts"] if p.get("type") == "tool"]
    assert len(tool_parts) == 1
    assert tool_parts[0]["id"] == "call_1"
    assert tool_parts[0]["result"] == marker


def test_stream_closes_a_tool_call_that_never_runs(signed_in, monkeypatch):
    """A streamed call the graph never runs must not spin forever."""
    import app.main as main
    from types import SimpleNamespace

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()

    def chunk(name, args, call_id):
        return SimpleNamespace(
            content="",
            additional_kwargs={},
            tool_call_chunks=[
                {
                    "name": name,
                    "args": args,
                    "id": call_id,
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ],
        )

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            yield {"event": "on_chat_model_start", "data": {}}
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": chunk("made_up_tool", "", "call_x")},
            }
            # The graph rejected the call; the next turn starts without a run.
            yield {"event": "on_chat_model_start", "data": {}}

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
    events = _sse_events(res.text)
    assert events["tool_call"][0]["id"] == "call_x"
    assert events["tool_result"][0]["id"] == "call_x"
    assert events["tool_result"][0]["ok"] is False


def test_nonstream_chat_persists_webpage_part(signed_in, monkeypatch):
    import app.main as main
    from langchain_core.messages import AIMessage, ToolMessage

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    marker = _marker("site-1", "index.html", ["index.html"])

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
    assert webpage_parts[0]["webpageId"] == "site-1"
    tool_parts = [p for p in parts if p.get("type") == "tool"]
    assert "content" not in tool_parts[0]["args"]
