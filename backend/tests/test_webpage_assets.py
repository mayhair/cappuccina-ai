"""Page assets: a generated picture or a user attachment copied into the workspace."""

import json

from app import db, sitebox

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


def _site(user, chat):
    site = db.get_webpage_site(user["id"], chat["id"])
    assert site is not None
    return site


def _workspace(user, chat):
    return sitebox.workspace_dir(_site(user, chat)["id"])


def _signed_in_user_chat(signed_in):
    """A chat owned by the signed_in fixture's user (kid@example.com)."""
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    return user, chat


# ---------- the tool ----------


def test_copies_the_exact_bytes_and_returns_the_shape(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)

    data = json.loads(_add(user, chat, "cat.png", image_id=_image(user, chat)["id"]))
    assert data["status"] == "webpage_asset_added"
    assert data["version_id"] is None
    assert data["webpage_id"] == _site(user, chat)["id"]
    assert data["path"] == "cat.png"
    assert data["mime"] == "image/png"
    assert data["bytes"] == len(PNG)
    assert data["files"] == ["cat.png", "index.html"]
    assert data["assets"] == []
    # The bytes are the source's, stored under the name the page will use.
    assert (_workspace(user, chat) / "cat.png").read_bytes() == PNG

    # A nested name works the same way.
    nested = json.loads(
        _add(user, chat, "img/pics/cat2.png", image_id=_image(user, chat)["id"])
    )
    assert nested["path"] == "img/pics/cat2.png"
    assert (_workspace(user, chat) / "img" / "pics" / "cat2.png").read_bytes() == PNG


def test_attachments_keep_their_own_extension_and_mime(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    photo = _attachment(user, chat, "photo.jpg", JPG, kind="image", mime="image/jpeg")
    report = _attachment(user, chat, "report.pdf", PDF, mime="application/pdf")

    added = json.loads(_add(user, chat, "img/photo.jpg", attachment_id=photo["id"]))
    assert added["path"] == "img/photo.jpg"
    assert added["mime"] == "image/jpeg"
    assert (_workspace(user, chat) / "img" / "photo.jpg").read_bytes() == JPG

    doc = json.loads(_add(user, chat, "files/report.pdf", attachment_id=report["id"]))
    assert doc["path"] == "files/report.pdf"
    assert doc["mime"] == "application/pdf"
    assert sorted(doc["files"]) == [
        "files/report.pdf",
        "img/photo.jpg",
        "index.html",
    ]


def test_an_unreadable_media_attachment_is_copied(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    song = _attachment(
        user, chat, "song.mp3", b"ID3\x00fake-audio",
        kind="asset", mime="audio/mpeg",
    )

    added = json.loads(_add(user, chat, "audio/song.mp3", attachment_id=song["id"]))
    assert added["path"] == "audio/song.mp3"
    assert added["mime"] == "audio/mpeg"
    assert added["bytes"] == len(b"ID3\x00fake-audio")
    assert (_workspace(user, chat) / "audio" / "song.mp3").read_bytes() == b"ID3\x00fake-audio"


def test_sources_are_checked(isolated_db):
    user, chat = _user_and_chat()
    image = _image(user, chat)

    assert "which picture" in _add(user, chat, "cat.png")
    assert "not both" in _add(user, chat, "cat.png", image_id="a", attachment_id="b")
    assert "couldn't find" in _add(user, chat, "cat.png", image_id="missing")
    assert "couldn't find" in _add(user, chat, "cat.png", attachment_id="missing")
    # The source is resolved before the name is judged.
    assert "couldn't find" in _add(user, chat, "cat.pdf", image_id="missing")


def test_a_site_must_exist_first(isolated_db):
    user, chat = _user_and_chat()
    image = _image(user, chat)

    out = _add(user, chat, "cat.png", image_id=image["id"])
    assert "There's no webpage yet" in out
    assert "write_webpage_file" in out


def test_the_name_must_keep_the_sources_extension(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    image = _image(user, chat)

    # The page can't ask for a ".png" that is really a PDF, or one that
    # escapes through ".." / a space / anything else the rules reject.
    assert "ending in .png" in _add(user, chat, "cat.pdf", image_id=image["id"])
    assert "ending in .png" in _add(user, chat, "../cat.png", image_id=image["id"])
    assert "ending in .png" in _add(user, chat, "bad name.png", image_id=image["id"])
    assert "ending in .png" in _add(user, chat, ".git/cat.png", image_id=image["id"])


def test_an_existing_name_is_refused(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    image = _image(user, chat)

    assert json.loads(_add(user, chat, "cat.png", image_id=image["id"]))["path"] == "cat.png"
    assert "already has cat.png" in _add(user, chat, "cat.png", image_id=image["id"])

    # A name the page already uses for text is refused the same way.
    notes = _attachment(user, chat, "notes.txt", b"hello", mime="text/plain", text="hello")
    _write(user, chat, "notes.txt", "page notes", reply_id="reply-2")
    assert "Pick a different name" in _add(
        user, chat, "notes.txt", reply_id="reply-2", attachment_id=notes["id"]
    )


def test_asset_size_cap(isolated_db, monkeypatch):
    from agent import tools

    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    image = _image(user, chat, raw=b"x" * 500)

    monkeypatch.setattr(tools, "_WEBPAGE_ASSET_LIMIT", 400)
    out = _add(user, chat, "cat.png", image_id=image["id"])
    assert out == "That file is too big for the page folder (20 MB max)."


def test_a_copied_picture_reads_back_as_a_picture(isolated_db):
    from agent import tools

    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    _add(user, chat, "cat.png", image_id=_image(user, chat)["id"])

    out = tools.read_webpage_file.invoke(
        {"path": "cat.png"}, config=_config(user, chat)
    )
    assert "cat.png is a picture in the site folder" in out
    assert "image/png" in out
    listed = tools.read_webpage_file.invoke(
        {"path": "nope.txt"}, config=_config(user, chat)
    )
    assert "cat.png" in listed and "index.html" in listed


def test_the_result_marks_the_site_without_a_file_path(isolated_db):
    import app.main as main

    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX)
    data = json.loads(_add(user, chat, "cat.png", image_id=_image(user, chat)["id"]))

    part = main._webpage_part_from_result(json.dumps(data))
    assert part == {
        "type": "webpage",
        "webpageId": data["webpage_id"],
        "files": ["cat.png", "index.html"],
    }
    assert "path" not in part


# ---------- API ----------


def test_the_env_lists_the_asset_as_an_ordinary_file(signed_in):
    from agent import tools

    user, chat = _signed_in_user_chat(signed_in)
    config = _config(user, chat, reply_id="reply-route")
    tools.write_webpage_file.invoke({"path": "index.html", "content": INDEX}, config=config)
    tools.add_webpage_asset.invoke(
        {"path": "cat.png", "image_id": _image(user, chat)["id"]}, config=config
    )

    env = signed_in.get(f"/api/v1/webpages/{chat['id']}").json()
    assert env["files"] == ["cat.png", "index.html"]
    # Metadata only: the bytes never ride in the sync payload.
    assert "data" not in json.dumps(env)


def test_the_asset_is_served_by_the_preview_route(signed_in):
    from agent import tools

    user, chat = _signed_in_user_chat(signed_in)
    config = _config(user, chat, reply_id="reply-preview")
    tools.write_webpage_file.invoke({"path": "index.html", "content": INDEX}, config=config)
    tools.add_webpage_asset.invoke(
        {"path": "cat.png", "image_id": _image(user, chat)["id"]}, config=config
    )

    site = _site(user, chat)
    base = f"/api/v1/webpages/{chat['id']}/preview/{site['preview_key']}"
    res = signed_in.get(f"{base}/cat.png")
    assert res.status_code == 200
    assert res.content == PNG
    assert res.headers["content-type"] == "image/png"
    assert res.headers["access-control-allow-origin"] == "*"
    # A wrong key (or another chat) is a plain 404.
    assert signed_in.get(f"/api/v1/webpages/{chat['id']}/preview/nope/cat.png").status_code == 404
