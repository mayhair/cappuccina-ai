"""Git history: one iteration per AI run, manual saves, activation, pruning."""

import json

from app import db, sitebox, webpage_git

INDEX = "<!doctype html>\n<title>Cat zone</title>"
INDEX_V2 = "<!doctype html>\n<title>Dog zone</title>\n<h1>Hello</h1>"
INDEX_V3 = INDEX_V2 + "\n<!-- third -->"
CSS = "body { color: red; }"
# NUL bytes make git treat this as binary (the Changes tab then says so).
PNG = b"\x89PNG\r\n\x1a\n\x00\x00" + b"p" * 100


def _user_and_chat(email="versions@example.com", sub="google-sub-versions"):
    user = db.create_google_user(email, "Versioner", sub)
    chat = db.create_chat(user["id"], title="Versions")
    return user, chat


def _config(user, chat, reply_id=None):
    configurable = {"user_id": user["id"], "chat_id": chat["id"]}
    if reply_id is not None:
        configurable["reply_id"] = reply_id
    return {"configurable": configurable}


def _write(user, chat, path, content, reply_id=None):
    from agent import tools

    return json.loads(
        tools.write_webpage_file.invoke(
            {"path": path, "content": content}, config=_config(user, chat, reply_id)
        )
    )


def _finalize(user, chat, reply_id):
    return sitebox.finalize_run(user["id"], chat["id"], reply_id)


def _site(user, chat):
    site = db.get_webpage_site(user["id"], chat["id"])
    assert site is not None
    return site


def _workspace(user, chat):
    return sitebox.workspace_dir(_site(user, chat)["id"])


def _versions(user, chat):
    return webpage_git.list_versions(_site(user, chat))


def _signed_in_user_chat(signed_in):
    """A chat owned by the signed_in fixture's user (kid@example.com)."""
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    return user, chat


# ---------- one iteration per AI run ----------


def test_one_commit_per_run_folds_its_writes(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX, "reply-1")
    _write(user, chat, "styles.css", CSS, "reply-1")

    sha, build = _finalize(user, chat, "reply-1")
    assert sha
    assert build is None  # no package.json, so no rebuild
    assert _site(user, chat)["active_commit"] == sha

    versions = _versions(user, chat)
    assert len(versions) == 1
    v1 = versions[0]
    assert v1["id"] == sha
    assert v1["seq"] == 1
    assert v1["origin"] == "ai"
    assert v1["messageUid"] == "reply-1"
    assert v1["parentVersionId"] is None
    assert v1["label"] == ""
    assert v1["assets"] == []
    # The seeded .gitignore is plumbing, not one of the run's files.
    assert v1["summary"]["files"] == 2
    assert v1["createdAt"]

    # A run that changed nothing makes no empty commit.
    assert _finalize(user, chat, "reply-1") == (None, None)


def test_a_second_run_parents_the_first(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX, "reply-1")
    v1, _ = _finalize(user, chat, "reply-1")
    _write(user, chat, "index.html", INDEX_V2, "reply-2")
    v2, _ = _finalize(user, chat, "reply-2")

    versions = _versions(user, chat)
    assert [v["seq"] for v in versions] == [1, 2]
    assert versions[1]["parentVersionId"] == v1
    assert versions[1]["id"] == v2
    assert versions[1]["messageUid"] == "reply-2"
    assert versions[1]["summary"]["added"] > 0
    assert versions[1]["summary"]["removed"] > 0


# ---------- manual saves ----------


def test_manual_save_creates_its_own_iteration(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    _write(user, chat, "styles.css", CSS, "reply-1")
    v1, _ = _finalize(user, chat, "reply-1")

    res = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/files",
        json={
            "path": "index.html",
            "content": "<h1>Hand edited</h1>",
            "baseVersionId": v1,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["files"] == ["index.html", "styles.css"]
    assert body["activeVersionId"] != v1
    assert (_workspace(user, chat) / "index.html").read_text(encoding="utf-8") == (
        "<h1>Hand edited</h1>"
    )

    versions = _versions(user, chat)
    assert [v["seq"] for v in versions] == [1, 2]
    manual = versions[1]
    assert manual["id"] == body["activeVersionId"]
    assert manual["origin"] == "manual"
    assert manual["label"] == "Edited index.html"
    assert manual["messageUid"] is None
    assert manual["parentVersionId"] == v1
    assert _site(user, chat)["active_commit"] == manual["id"]


def test_a_stale_base_is_refused_and_the_file_kept(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    stale, _ = _finalize(user, chat, "reply-1")
    _write(user, chat, "index.html", INDEX_V2, "reply-2")
    _finalize(user, chat, "reply-2")

    res = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/files",
        json={"path": "index.html", "content": INDEX_V3, "baseVersionId": stale},
    )
    assert res.status_code == 409
    assert res.json()["detail"] == (
        "The page changed since you opened it. Reload the canvas and try again."
    )
    assert (_workspace(user, chat) / "index.html").read_text(encoding="utf-8") == INDEX_V2
    assert [v["seq"] for v in _versions(user, chat)] == [1, 2]


def test_manual_save_validates_like_the_tool(signed_in):
    from agent import tools

    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    active, _ = _finalize(user, chat, "reply-1")

    def save(body):
        return signed_in.put(f"/api/v1/webpages/{chat['id']}/files", json=body)

    bad_path = save({"path": "../evil.html", "content": INDEX, "baseVersionId": active})
    assert bad_path.status_code == 400
    assert "isn't allowed" in bad_path.json()["detail"]
    empty = save({"path": "index.html", "content": "", "baseVersionId": active})
    assert empty.status_code == 400
    assert "empty" in empty.json()["detail"]
    oversized = save(
        {
            "path": "index.html",
            "content": "x" * (tools._WEBPAGE_FILE_LIMIT + 1),
            "baseVersionId": active,
        }
    )
    assert oversized.status_code == 400
    assert "too big" in oversized.json()["detail"]
    # Nothing was written and no iteration was added.
    assert [v["seq"] for v in _versions(user, chat)] == [1]


def test_manual_save_without_a_page_is_404(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    res = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/files",
        json={"path": "index.html", "content": INDEX, "baseVersionId": "v0"},
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "No webpage yet"


# ---------- activation and branching ----------


def test_activation_restores_the_snapshot_and_later_commits_branch(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    _write(user, chat, "styles.css", CSS, "reply-1")
    v1, _ = _finalize(user, chat, "reply-1")
    _write(user, chat, "index.html", INDEX_V2, "reply-2")
    _write(user, chat, "extra.txt", "added later", "reply-2")
    v2, _ = _finalize(user, chat, "reply-2")
    assert v2 != v1

    res = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/active", json={"versionId": v1}
    )
    assert res.status_code == 200
    assert res.json()["activeVersionId"] == v1
    assert res.json()["files"] == ["index.html", "styles.css"]

    # The working copy is the snapshot exactly: the later file is gone.
    workspace = _workspace(user, chat)
    assert (workspace / "index.html").read_text(encoding="utf-8") == INDEX
    assert (workspace / "styles.css").read_text(encoding="utf-8") == CSS
    assert not (workspace / "extra.txt").exists()

    # A commit after an activation branches off it and keeps the others listed.
    _write(user, chat, "index.html", INDEX_V3, "reply-3")
    v3, _ = _finalize(user, chat, "reply-3")
    versions = _versions(user, chat)
    assert [v["seq"] for v in versions] == [1, 2, 3]
    assert [v["id"] for v in versions] == [v1, v2, v3]
    assert versions[2]["parentVersionId"] == v1
    assert _site(user, chat)["active_commit"] == v3


def test_activation_rejects_unknown_and_foreign_iterations(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    _finalize(user, chat, "reply-1")

    bogus = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/active", json={"versionId": "nope"}
    )
    assert bogus.status_code == 404
    assert bogus.json()["detail"] == "Iteration not found"

    # An attempt to activate a commit that exists but carries no iter/ tag
    # (here: the empty tree) is refused the same way.
    other = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/active",
        json={"versionId": webpage_git.EMPTY_TREE},
    )
    assert other.status_code == 404


# ---------- scoping ----------


def test_version_routes_require_a_session(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    version_id, _ = _finalize(user, chat, "reply-1")
    assert signed_in.post("/api/v1/auth/logout").json() == {"ok": True}

    assert signed_in.get(f"/api/v1/webpages/{chat['id']}/versions").status_code == 401
    assert (
        signed_in.put(
            f"/api/v1/webpages/{chat['id']}/active", json={"versionId": version_id}
        ).status_code
        == 401
    )
    assert (
        signed_in.put(
            f"/api/v1/webpages/{chat['id']}/files",
            json={"path": "index.html", "content": INDEX, "baseVersionId": version_id},
        ).status_code
        == 401
    )
    assert (
        signed_in.get(
            f"/api/v1/webpages/{chat['id']}/versions/{version_id}/diff"
        ).status_code
        == 401
    )


def test_version_routes_are_404_for_a_strangers_chat(signed_in):
    stranger = db.create_google_user("stranger-w@example.com", "Stranger", "sub-w")
    chat = db.create_chat(stranger["id"], title="Theirs")
    from agent import tools

    tools.write_webpage_file.invoke(
        {"path": "index.html", "content": INDEX}, config=_config(stranger, chat)
    )
    version_id, _ = sitebox.finalize_run(stranger["id"], chat["id"], None)

    assert signed_in.get(f"/api/v1/webpages/{chat['id']}/versions").status_code == 404
    assert (
        signed_in.put(
            f"/api/v1/webpages/{chat['id']}/active", json={"versionId": version_id}
        ).status_code
        == 404
    )
    assert (
        signed_in.put(
            f"/api/v1/webpages/{chat['id']}/files",
            json={"path": "index.html", "content": INDEX, "baseVersionId": version_id},
        ).status_code
        == 404
    )
    assert (
        signed_in.get(
            f"/api/v1/webpages/{chat['id']}/versions/{version_id}/diff"
        ).status_code
        == 404
    )


def test_diff_for_an_unknown_iteration_is_404(signed_in):
    _user, chat = _signed_in_user_chat(signed_in)
    res = signed_in.get(f"/api/v1/webpages/{chat['id']}/versions/nope/diff")
    assert res.status_code == 404
    assert res.json()["detail"] == "Iteration not found"


# ---------- busy guard ----------


def test_mutations_are_refused_while_a_reply_streams(signed_in):
    import app.main as main

    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    active, _ = _finalize(user, chat, "reply-1")

    main._WEBPAGE_BUSY.add(chat["id"])
    try:
        activated = signed_in.put(
            f"/api/v1/webpages/{chat['id']}/active", json={"versionId": active}
        )
        assert activated.status_code == 409
        assert activated.json()["detail"] == (
            "A reply is still being written. Wait for it to finish."
        )
        saved = signed_in.put(
            f"/api/v1/webpages/{chat['id']}/files",
            json={"path": "index.html", "content": INDEX_V2, "baseVersionId": active},
        )
        assert saved.status_code == 409
        # Reads stay available while the reply is written.
        assert signed_in.get(f"/api/v1/webpages/{chat['id']}/versions").status_code == 200
    finally:
        main._WEBPAGE_BUSY.discard(chat["id"])


# ---------- pruning ----------


def test_pruning_keeps_fifty_and_the_active_iteration(isolated_db):
    user, chat = _user_and_chat()
    site = db.create_webpage_site(user["id"], chat["id"], "prune-key")
    workspace = sitebox.workspace_dir(site["id"])
    webpage_git.init_workspace(workspace)

    shas = []
    for i in range(1, 54):
        (workspace / "index.html").write_text(f"<h1>{i}</h1>", encoding="utf-8")
        shas.append(
            webpage_git.commit_workspace(site, origin="ai", label="", message_uid=None)
        )

    site = _site(user, chat)
    versions = webpage_git.list_versions(site)
    assert len(versions) == 50
    assert [v["seq"] for v in versions] == list(range(4, 54))
    # The three oldest iterations were retired (their tags deleted) ...
    assert not webpage_git.is_version(site, shas[0])
    assert not webpage_git.is_version(site, shas[2])
    # ... and the active one is the newest, always kept.
    assert webpage_git.is_version(site, shas[-1])
    assert site["active_commit"] == shas[-1]
    assert versions[-1]["id"] == shas[-1]


# ---------- diff ----------


def test_the_diff_route_describes_changes_and_binary_files(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    v1, _ = _finalize(user, chat, "reply-1")
    _write(user, chat, "index.html", INDEX_V2, "reply-2")
    (_workspace(user, chat) / "logo.png").write_bytes(PNG)
    v2, _ = _finalize(user, chat, "reply-2")

    res = signed_in.get(f"/api/v1/webpages/{chat['id']}/versions/{v2}/diff")
    assert res.status_code == 200
    diff = res.json()
    assert diff["versionId"] == v2
    assert diff["baseVersionId"] == v1
    assert diff["truncated"] is False

    files = {f["path"]: f for f in diff["files"]}
    assert sorted(files) == ["index.html", "logo.png"]

    entry = files["index.html"]
    assert entry["status"] == "modified"
    kinds = [line["kind"] for line in entry["lines"]]
    assert "hunk" in kinds and "add" in kinds and "del" in kinds
    assert entry["added"] == sum(1 for k in kinds if k == "add")
    assert entry["removed"] == sum(1 for k in kinds if k == "del")

    logo = files["logo.png"]
    assert logo["status"] == "added"
    assert logo["added"] == 0 and logo["removed"] == 0
    assert logo["lines"] == [{"kind": "ctx", "text": "binary file"}]

    # An explicit base compares any two iterations, and a root commit is all
    # additions against the empty tree.
    root = signed_in.get(
        f"/api/v1/webpages/{chat['id']}/versions/{v1}/diff"
    ).json()
    assert root["baseVersionId"] is None
    assert root["files"][0]["status"] == "added"


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


def test_stream_commits_the_reply_as_one_iteration(signed_in, monkeypatch):
    import app.main as main
    from types import SimpleNamespace

    from agent import tools

    user, chat = _signed_in_user_chat(signed_in)

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            # The tool really runs, so the marker and the commit refer to the
            # same workspace.
            marker = tools.write_webpage_file.invoke(
                {"path": "index.html", "content": INDEX}, config=config
            )
            yield {"event": "on_chat_model_start", "data": {}}
            yield {
                "event": "on_tool_start",
                "run_id": "r1",
                "name": "write_webpage_file",
                "data": {"input": {"path": "index.html", "content": INDEX}},
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
    version_id = events["webpage"][-1]["versionId"]
    assert version_id
    assert _site(user, chat)["active_commit"] == version_id

    versions = _versions(user, chat)
    assert len(versions) == 1
    assert versions[0]["id"] == version_id
    assert versions[0]["messageUid"] == "reply-stream"

    # The reply is finished: the chat is no longer busy.
    assert chat["id"] not in main._WEBPAGE_BUSY
