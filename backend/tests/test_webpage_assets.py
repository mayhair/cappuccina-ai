"""Page assets: a generated picture or a user attachment copied into the folder."""

import json

from app import db

INDEX = "<!doctype html>\n<title>Cat zone</title>\n<img src=\"cat.png\">"
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 900
JPG = b"\xff\xd8\xff" + b"j" * 400
PDF = b"%PDF-1.4 fake"


def _user_and_chat(email="assets@example.com", sub="google-sub-assets"):
    user = db.create_google_user(email, "Asset builder", sub)
    chat = db.create_chat(user["id"], title="Assets")
    return user, chat


def _config(user, chat, reply_id=None):
    configurable = {"user_id": user["id"], "chat_id": chat["id"]}
    if reply_id is not None:
        configurable["reply_id"] = reply_id
    return {"configurable": configurable}


def _write(user, chat, path, content, reply_id="reply-1"):
    from agent import tools

    return json.loads(
        tools.write_webpage_file.invoke(
            {"path": path, "content": content},
            config=_config(user, chat, reply_id=reply_id),
        )
    )


def _add(user, chat, path, reply_id="reply-1", **source):
    from agent import tools

    return tools.add_webpage_asset.invoke(
        {"path": path, **source}, config=_config(user, chat, reply_id=reply_id)
    )


def _image(user, chat, raw=PNG, mime="image/png"):
    return db.create_image(user["id"], chat["id"], raw, mime=mime, prompt="a cat")


def _attachment(user, chat, filename, raw, kind="document", mime="application/pdf", text=""):
    return db.create_attachment(
        user["id"], chat["id"], filename, raw, kind=kind, mime=mime, text=text
    )


def _signed_in_user_chat(signed_in):
    """A chat owned by the signed_in fixture's user (kid@example.com)."""
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    return user, chat


# ---------- the tool ----------


def test_generated_picture_joins_the_runs_iteration(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    image = _image(user, chat)

    data = json.loads(_add(user, chat, "cat.png", image_id=image["id"]))
    assert data["status"] == "webpage_asset_added"
    assert data["path"] == "cat.png"
    assert data["mime"] == "image/png"
    assert data["bytes"] == len(PNG)
    assert data["files"] == ["index.html"]
    assert data["assets"] == ["cat.png"]

    # One reply is one iteration, whether it wrote files, pictures or both.
    versions = db.list_webpage_versions(user["id"], chat["id"])
    assert len(versions) == 1
    assert versions[0]["id"] == data["version_id"]
    assert versions[0]["assets"] == ["cat.png"]
    assert versions[0]["summary"] == {
        "files": 2,
        "added": len(INDEX.splitlines()) + 1,
        "removed": 0,
    }

    env = db.get_webpage_env(user["id"], chat["id"])
    assert env["files"] == {"index.html": INDEX}
    assert env["assets"] == {"cat.png": {"mime": "image/png", "size": len(PNG)}}
    assert db.get_webpage_active_version(user["id"], chat["id"]) == data["version_id"]


def test_attachments_keep_their_own_extension(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    photo = _attachment(user, chat, "photo.jpg", JPG, kind="image", mime="image/jpeg")
    report = _attachment(user, chat, "report.pdf", PDF, kind="document", mime="application/pdf")

    added = json.loads(_add(user, chat, "pics/photo.jpg", attachment_id=photo["id"]))
    assert added["path"] == "pics/photo.jpg"
    assert added["mime"] == "image/jpeg"
    doc = json.loads(_add(user, chat, "files/report.pdf", attachment_id=report["id"]))
    assert doc["path"] == "files/report.pdf"
    assert doc["mime"] == "application/pdf"

    env = db.get_webpage_env(user["id"], chat["id"])
    assert sorted(env["assets"]) == ["files/report.pdf", "pics/photo.jpg"]
    # The bytes are the attachment's, stored once for the page.
    row = db.get_webpage_asset(user["id"], chat["id"], "pics/photo.jpg")
    assert row["data"] == JPG


def test_asset_names_and_sources_are_checked(isolated_db):
    user, chat = _user_and_chat()
    image = _image(user, chat)
    # The source is resolved first: a bogus id fails before the missing page does.
    assert "couldn't find" in _add(user, chat, "cat.png", image_id="missing")
    assert "no webpage yet" in _add(user, chat, "cat.png", image_id=image["id"])
    _write(user, chat, "index.html", INDEX)

    assert "which picture" in _add(user, chat, "cat.png")
    assert "not both" in _add(user, chat, "cat.png", image_id="a", attachment_id="b")
    assert "couldn't find" in _add(user, chat, "cat.png", attachment_id="missing")
    # The name has to keep the file's own type, so the page can't ask for a
    # ".png" that is really a PDF.
    assert "ending in .png" in _add(user, chat, "cat.pdf", image_id=image["id"])
    assert "ending in .png" in _add(user, chat, "../cat.png", image_id=image["id"])

    assert json.loads(_add(user, chat, "cat.png", image_id=image["id"]))["path"] == "cat.png"
    # The same name twice is refused.
    assert "already has cat.png" in _add(user, chat, "cat.png", image_id=image["id"])
    # So is a name the page already uses for text — reachable for the text-like
    # extensions an attachment can carry (here a .txt file the page also has).
    notes = _attachment(user, chat, "notes.txt", b"hello", mime="text/plain", text="hello")
    _write(user, chat, "notes.txt", "page notes", reply_id="reply-2")
    assert "Pick a different name" in _add(
        user, chat, "notes.txt", reply_id="reply-2", attachment_id=notes["id"]
    )


def test_asset_size_caps(isolated_db, monkeypatch):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    big = _image(user, chat, raw=b"x" * 500)
    monkeypatch.setattr(db, "WEBPAGE_MAX_ASSET_BYTES", 400)
    assert "too big" in _add(user, chat, "cat.png", image_id=big["id"])

    monkeypatch.setattr(db, "WEBPAGE_MAX_ASSET_BYTES", 10_000)
    monkeypatch.setattr(db, "WEBPAGE_MAX_ASSET_TOTAL_BYTES", 900)
    assert json.loads(_add(user, chat, "cat.png", image_id=big["id"]))["path"] == "cat.png"
    assert "folder is full" in _add(user, chat, "dog.png", image_id=big["id"])


def test_reading_a_picture_points_back_at_the_page(isolated_db):
    from agent import tools

    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    _add(user, chat, "cat.png", image_id=_image(user, chat)["id"])

    out = tools.read_webpage_file.invoke({"path": "cat.png"}, config=_config(user, chat))
    assert "picture in the page folder" in out
    listed = tools.read_webpage_file.invoke({"path": "nope.txt"}, config=_config(user, chat))
    assert "pictures: cat.png" in listed


# ---------- iterations ----------


def test_asset_is_diffed_and_kept_across_iterations(isolated_db):
    user, chat = _user_and_chat()
    v1 = _write(user, chat, "index.html", INDEX)
    image = _image(user, chat)
    v2 = json.loads(_add(user, chat, "cat.png", reply_id="reply-2", image_id=image["id"]))

    versions = db.list_webpage_versions(user["id"], chat["id"])
    assert [v["seq"] for v in versions] == [1, 2]
    assert versions[1]["parentVersionId"] == v1["version_id"]
    assert versions[0]["assets"] == []

    diff = db.webpage_version_diff(user["id"], chat["id"], v2["version_id"])
    assert [f["path"] for f in diff["files"]] == ["cat.png"]
    assert diff["files"][0]["status"] == "added"
    assert diff["files"][0]["added"] == 1
    assert "cat.png" in diff["files"][0]["lines"][0]["text"]
    assert "image/png" in diff["files"][0]["lines"][0]["text"]

    # A later run that only edits text does not re-report the picture.
    v3 = _write(user, chat, "index.html", INDEX + "\n<!-- more -->", reply_id="reply-3")
    assert v3["version_id"]
    after = db.list_webpage_versions(user["id"], chat["id"])
    assert [v["seq"] for v in after] == [1, 2, 3]
    assert after[-1]["assets"] == ["cat.png"]
    later = db.webpage_version_diff(user["id"], chat["id"], after[-1]["id"])
    assert [f["path"] for f in later["files"]] == ["index.html"]

    # Going back to the first iteration keeps the picture in the folder: the
    # bytes are the environment's, only the iteration's list changes.
    db.set_webpage_active_version(user["id"], chat["id"], v1["version_id"])
    env = db.get_webpage_env(user["id"], chat["id"])
    assert env["files"] == {"index.html": INDEX}
    assert list(env["assets"]) == ["cat.png"]


def test_dropping_an_asset_reads_as_removed(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    added = json.loads(_add(user, chat, "cat.png", image_id=_image(user, chat)["id"]))

    # No tool removes an asset, but a commit without it must diff cleanly.
    dropped = db.commit_webpage_version(
        user["id"], chat["id"], {"index.html": INDEX}, assets=[], origin="manual"
    )
    diff = db.webpage_version_diff(user["id"], chat["id"], dropped["id"])
    assert diff["files"][0]["path"] == "cat.png"
    assert diff["files"][0]["status"] == "removed"
    assert diff["files"][0]["removed"] == 1
    assert dropped["assets"] == []
    assert db.get_webpage_asset(user["id"], chat["id"], "cat.png") is not None
    assert added["version_id"] != dropped["id"]


def test_assets_survive_iteration_pruning(isolated_db, monkeypatch):
    user, chat = _user_and_chat()
    monkeypatch.setattr(db, "WEBPAGE_VERSION_LIMIT", 3)
    _write(user, chat, "index.html", INDEX, reply_id="r0")
    for i in range(1, 5):
        _add(user, chat, f"pic{i}.png", reply_id=f"r{i}", image_id=_image(user, chat)["id"])

    versions = db.list_webpage_versions(user["id"], chat["id"])
    assert len(versions) == 3
    active = db.get_webpage_active_version(user["id"], chat["id"])
    assert versions[-1]["id"] == active
    assert versions[-1]["assets"] == ["pic1.png", "pic2.png", "pic3.png", "pic4.png"]
    assert db.get_webpage_asset(user["id"], chat["id"], "pic1.png") is not None


# ---------- routes ----------


def test_asset_route_serves_bytes_and_is_scoped(signed_in):
    from agent import tools

    user, chat = _signed_in_user_chat(signed_in)
    config = _config(user, chat, reply_id="reply-route")
    tools.write_webpage_file.invoke({"path": "index.html", "content": INDEX}, config=config)
    image = _image(user, chat)
    tools.add_webpage_asset.invoke(
        {"path": "cat.png", "image_id": image["id"]}, config=config
    )

    env = signed_in.get(f"/api/v1/webpages/{chat['id']}").json()
    assert env["assets"] == {"cat.png": {"mime": "image/png", "size": len(PNG)}}
    assert env["files"] == {"index.html": INDEX}
    # The metadata is not the bytes.
    assert "data" not in json.dumps(env)

    res = signed_in.get(f"/api/v1/webpages/{chat['id']}/assets/cat.png")
    assert res.status_code == 200
    assert res.content == PNG
    assert res.headers["content-type"] == "image/png"

    assert signed_in.get(f"/api/v1/webpages/{chat['id']}/assets/dog.png").status_code == 404
    other = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    assert signed_in.get(f"/api/v1/webpages/{other['id']}/assets/cat.png").status_code == 404
    signed_in.cookies.clear()
    assert signed_in.get(f"/api/v1/webpages/{chat['id']}/assets/cat.png").status_code == 401


def test_asset_route_is_user_scoped_across_accounts(signed_in):
    from agent import tools

    owner, chat = _signed_in_user_chat(signed_in)
    tools.write_webpage_file.invoke(
        {"path": "index.html", "content": INDEX}, config=_config(owner, chat)
    )
    tools.add_webpage_asset.invoke(
        {"path": "cat.png", "image_id": _image(owner, chat)["id"]},
        config=_config(owner, chat),
    )
    stranger = db.create_google_user("stranger@example.com", "Stranger", "google-sub-stranger")
    assert db.get_webpage_asset(stranger["id"], chat["id"], "cat.png") is None
    assert db.get_webpage_env(stranger["id"], chat["id"]) is None


# ---------- streaming ----------


def test_streaming_announces_an_asset_without_file_text(signed_in, monkeypatch):
    from types import SimpleNamespace

    import app.main as main
    from agent import tools

    user, chat = _signed_in_user_chat(signed_in)
    config = _config(user, chat, reply_id="reply-stream")
    tools.write_webpage_file.invoke({"path": "index.html", "content": INDEX}, config=config)
    image = _image(user, chat)

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            marker = tools.add_webpage_asset.invoke(
                {"path": "cat.png", "image_id": image["id"]}, config=config
            )
            yield {"event": "on_chat_model_start", "data": {}}
            yield {
                "event": "on_tool_start",
                "run_id": "r1",
                "name": "add_webpage_asset",
                "data": {"input": {"path": "cat.png", "image_id": image["id"]}},
            }
            yield {
                "event": "on_tool_end",
                "run_id": "r1",
                "name": "add_webpage_asset",
                "data": {"output": marker},
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="Added the picture!")},
            }

    monkeypatch.setattr(main, "GRAPH", FakeGraph())
    res = signed_in.post(
        "/api/v1/chat/stream",
        json={
            "chatId": chat["id"],
            "messages": [{"role": "user", "content": "put my cat on the page"}],
            "replyId": "reply-stream",
            "generateTitle": False,
        },
    )
    assert res.status_code == 200
    event = _sse_events(res.text)["webpage"][0]
    assert event["asset"] is True
    assert event["path"] == ""
    assert event["content"] == ""
    assert event["assets"] == {"cat.png": {"mime": "image/png", "size": len(PNG)}}
    assert event["versionId"]
    assert db.get_webpage_active_version(user["id"], chat["id"]) == event["versionId"]
    # The stored part marks the environment without inventing a file.
    stored = signed_in.get("/api/v1/chats").json()
    parts = stored[0]["messages"][-1]["parts"]
    webpage_parts = [p for p in parts if p.get("type") == "webpage"]
    assert webpage_parts and webpage_parts[-1].get("asset") is True
    assert "path" not in webpage_parts[-1]


def _sse_events(text):
    """{event: [payload, ...]} — the stream uses \r\n line endings."""
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
