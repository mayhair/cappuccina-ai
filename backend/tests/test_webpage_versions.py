"""Iterations (webpage_versions): one snapshot per AI run, activation, manual saves."""

import json

from app import db

INDEX = "<!doctype html>\n<title>Cat zone</title>"
INDEX_V2 = "<!doctype html>\n<title>Dog zone</title>\n<h1>Hello</h1>"
INDEX_V3 = INDEX_V2 + "\n<!-- third -->"
CSS = "body { color: red; }"


def _user_and_chat(email="versions@example.com", sub="google-sub-versions"):
    user = db.create_google_user(email, "Versioner", sub)
    chat = db.create_chat(user["id"], title="Versions")
    return user, chat


def _config(user, chat, reply_id=None):
    configurable = {"user_id": user["id"], "chat_id": chat["id"]}
    if reply_id is not None:
        configurable["reply_id"] = reply_id
    return {"configurable": configurable}


def _write(user, chat, path, content, reply_id):
    from agent import tools

    return json.loads(
        tools.write_webpage_file.invoke(
            {"path": path, "content": content},
            config=_config(user, chat, reply_id=reply_id),
        )
    )


def _signed_in_user_chat(signed_in):
    """A chat owned by the signed_in fixture's user (kid@example.com)."""
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    return user, chat


# ---------- one iteration per AI run ----------


def test_writes_in_one_run_share_one_iteration(isolated_db):
    user, chat = _user_and_chat()
    first = _write(user, chat, "index.html", INDEX, "reply-1")
    second = _write(user, chat, "styles.css", CSS, "reply-1")
    assert first["version_id"] == second["version_id"]

    versions = db.list_webpage_versions(user["id"], chat["id"])
    assert len(versions) == 1
    v1 = versions[0]
    assert v1["id"] == first["version_id"]
    assert v1["seq"] == 1
    assert v1["parentVersionId"] is None
    assert v1["origin"] == "ai"
    assert v1["messageUid"] == "reply-1"
    assert v1["summary"] == {
        "files": 2,
        "added": len(INDEX.splitlines()) + len(CSS.splitlines()),
        "removed": 0,
    }
    assert db.get_webpage_active_version(user["id"], chat["id"]) == v1["id"]
    # The working copy is the iteration's snapshot.
    env = db.get_webpage_env(user["id"], chat["id"])
    assert env["files"] == {"index.html": INDEX, "styles.css": CSS}


def test_write_without_reply_id_makes_its_own_iteration(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX, None)
    _write(user, chat, "styles.css", CSS, None)
    versions = db.list_webpage_versions(user["id"], chat["id"])
    assert [v["seq"] for v in versions] == [1, 2]


def test_second_run_parents_the_first(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX, "reply-1")
    _write(user, chat, "index.html", INDEX_V2, "reply-2")

    versions = db.list_webpage_versions(user["id"], chat["id"])
    assert [v["seq"] for v in versions] == [1, 2]
    v1, v2 = versions
    assert v2["parentVersionId"] == v1["id"]
    assert v2["messageUid"] == "reply-2"
    assert v2["summary"]["removed"] > 0
    assert v2["summary"]["added"] > 0


# ---------- activation and branching ----------


def test_activate_older_iteration_then_branch_keeps_later_ones(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    _write(user, chat, "styles.css", CSS, "reply-1")
    _write(user, chat, "index.html", INDEX_V2, "reply-2")
    v1, v2 = db.list_webpage_versions(user["id"], chat["id"])

    res = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/active", json={"versionId": v1["id"]}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["activeVersionId"] == v1["id"]
    assert body["files"] == {"index.html": INDEX, "styles.css": CSS}
    assert db.get_webpage_env(user["id"], chat["id"])["files"] == body["files"]

    _write(user, chat, "index.html", INDEX_V3, "reply-3")
    versions = db.list_webpage_versions(user["id"], chat["id"])
    assert [v["seq"] for v in versions] == [1, 2, 3]
    assert versions[0]["id"] == v1["id"]
    assert versions[1]["id"] == v2["id"]
    assert versions[2]["parentVersionId"] == v1["id"]
    assert db.get_webpage_active_version(user["id"], chat["id"]) == versions[2]["id"]


# ---------- manual saves ----------


def test_manual_save_creates_its_own_iteration(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    _write(user, chat, "styles.css", CSS, "reply-1")
    was = db.get_webpage_env(user["id"], chat["id"])

    res = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/files",
        json={
            "path": "index.html",
            "content": "<h1>Hand edited</h1>",
            "baseVersionId": was["active_version_id"],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["files"] == {"index.html": "<h1>Hand edited</h1>", "styles.css": CSS}
    assert body["activeVersionId"] != was["active_version_id"]
    assert db.get_webpage_env(user["id"], chat["id"])["files"] == body["files"]

    manual = db.get_webpage_version(user["id"], chat["id"], body["activeVersionId"])
    assert manual["origin"] == "manual"
    assert manual["label"] == "Edited index.html"
    assert manual["parentVersionId"] == was["active_version_id"]
    assert [v["seq"] for v in db.list_webpage_versions(user["id"], chat["id"])] == [1, 2]


def test_manual_save_refuses_a_stale_base(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    stale = db.get_webpage_env(user["id"], chat["id"])["active_version_id"]
    _write(user, chat, "index.html", INDEX_V2, "reply-2")

    res = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/files",
        json={"path": "index.html", "content": INDEX_V3, "baseVersionId": stale},
    )
    assert res.status_code == 409
    assert db.get_webpage_env(user["id"], chat["id"])["files"]["index.html"] == INDEX_V2


def test_manual_save_validates_like_the_tool(signed_in):
    from agent.tools import WEBPAGE_MAX_FILE_BYTES

    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    active = db.get_webpage_env(user["id"], chat["id"])["active_version_id"]

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
            "content": "x" * (WEBPAGE_MAX_FILE_BYTES + 1),
            "baseVersionId": active,
        }
    )
    assert oversized.status_code == 400
    assert "too big" in oversized.json()["detail"]
    # Nothing was written: the iteration list is unchanged.
    assert len(db.list_webpage_versions(user["id"], chat["id"])) == 1


def test_manual_save_without_a_page_is_404(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    res = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/files",
        json={"path": "index.html", "content": INDEX, "baseVersionId": "v0"},
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "No webpage yet"


# ---------- scoping ----------


def test_activation_rejects_unknown_and_foreign_iterations(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")

    bogus = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/active", json={"versionId": "nope"}
    )
    assert bogus.status_code == 404
    assert bogus.json()["detail"] == "Iteration not found"

    stranger = db.create_google_user("stranger-v@example.com", "Stranger", "sub-v")
    other_chat = db.create_chat(stranger["id"], title="Theirs")
    theirs = db.put_webpage_file(stranger["id"], other_chat["id"], "index.html", INDEX)
    foreign = signed_in.put(
        f"/api/v1/webpages/{chat['id']}/active",
        json={"versionId": theirs["version"]["id"]},
    )
    assert foreign.status_code == 404
    # And the other user's chat is invisible entirely.
    assert signed_in.get(f"/api/v1/webpages/{other_chat['id']}/versions").status_code == 404


def test_version_routes_require_a_session(signed_in):
    user, chat = _signed_in_user_chat(signed_in)
    _write(user, chat, "index.html", INDEX, "reply-1")
    version_id = db.get_webpage_active_version(user["id"], chat["id"])
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
    entry = db.put_webpage_file(stranger["id"], chat["id"], "index.html", INDEX)
    version_id = entry["version"]["id"]

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
    active = db.get_webpage_env(user["id"], chat["id"])["active_version_id"]

    main._WEBPAGE_BUSY.add(chat["id"])
    try:
        activated = signed_in.put(
            f"/api/v1/webpages/{chat['id']}/active", json={"versionId": active}
        )
        assert activated.status_code == 409
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


def test_prune_keeps_the_active_iteration_and_the_newest(isolated_db, monkeypatch):
    monkeypatch.setattr(db, "WEBPAGE_VERSION_LIMIT", 3)
    user, chat = _user_and_chat()
    for i in range(1, 6):
        _write(user, chat, "index.html", INDEX + f"\n<!-- {i} -->", f"reply-{i}")

    versions = db.list_webpage_versions(user["id"], chat["id"])
    assert [v["seq"] for v in versions] == [3, 4, 5]
    active = db.get_webpage_active_version(user["id"], chat["id"])
    assert active == versions[-1]["id"]
    assert db.get_webpage_version(user["id"], chat["id"], active) is not None


# ---------- diff ----------


def test_diff_of_the_root_iteration_is_all_added(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX, "reply-1")
    v1 = db.list_webpage_versions(user["id"], chat["id"])[0]

    diff = db.webpage_version_diff(user["id"], chat["id"], v1["id"])
    assert diff["versionId"] == v1["id"]
    assert diff["baseVersionId"] is None
    assert diff["truncated"] is False
    assert [f["path"] for f in diff["files"]] == ["index.html"]
    entry = diff["files"][0]
    assert entry["status"] == "added"
    assert entry["added"] == len(INDEX.splitlines())
    assert entry["removed"] == 0
    assert [line["kind"] for line in entry["lines"]] == ["add"] * len(INDEX.splitlines())
    assert [line["text"] for line in entry["lines"]] == INDEX.splitlines()


def test_diff_of_a_modified_file_counts_both_ways(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX, "reply-1")
    _write(user, chat, "index.html", INDEX_V2, "reply-2")
    v1, v2 = db.list_webpage_versions(user["id"], chat["id"])

    diff = db.webpage_version_diff(user["id"], chat["id"], v2["id"])
    assert diff["baseVersionId"] == v1["id"]
    entry = diff["files"][0]
    assert entry["status"] == "modified"
    kinds = [line["kind"] for line in entry["lines"]]
    assert "hunk" in kinds and "ctx" in kinds and "add" in kinds and "del" in kinds
    assert entry["removed"] == sum(1 for k in kinds if k == "del")
    assert entry["added"] == sum(1 for k in kinds if k == "add")

    # An explicit base compares any two iterations.
    against = db.webpage_version_diff(user["id"], chat["id"], v2["id"], against=v1["id"])
    assert against["baseVersionId"] == v1["id"]
    assert against["files"][0]["added"] == entry["added"]
    # The same iteration against itself has nothing to show.
    same = db.webpage_version_diff(user["id"], chat["id"], v2["id"], against=v2["id"])
    assert same["files"] == []


def test_diff_survives_a_pruned_parent(isolated_db):
    user, chat = _user_and_chat()
    _write(user, chat, "index.html", INDEX, "reply-1")
    _write(user, chat, "index.html", INDEX_V2, "reply-2")
    v1, v2 = db.list_webpage_versions(user["id"], chat["id"])

    db.get_conn().execute("DELETE FROM webpage_versions WHERE id = :id", {"id": v1["id"]})
    db.get_conn().commit()

    diff = db.webpage_version_diff(user["id"], chat["id"], v2["id"])
    assert diff["baseVersionId"] is None
    entry = diff["files"][0]
    assert entry["status"] == "added"
    assert entry["added"] == len(INDEX_V2.splitlines())
    assert [line["kind"] for line in entry["lines"]] == ["add"] * len(INDEX_V2.splitlines())


def test_migration_backfills_a_first_iteration(isolated_db):
    user, chat = _user_and_chat()
    # Legacy state: a working copy with no iterations (as a pre-change db).
    db.put_webpage_file(user["id"], chat["id"], "index.html", INDEX)
    conn = db.get_conn()
    conn.execute("DELETE FROM webpage_versions")
    conn.execute("UPDATE webpage_envs SET active_version_id = NULL")
    conn.commit()

    db._migrate_webpage_versions(conn)
    versions = db.list_webpage_versions(user["id"], chat["id"])
    assert len(versions) == 1
    assert versions[0]["seq"] == 1
    assert versions[0]["label"] == "Initial page"
    assert versions[0]["parentVersionId"] is None
    assert db.get_webpage_active_version(user["id"], chat["id"]) == versions[0]["id"]
    # Idempotent: a second run adds nothing.
    db._migrate_webpage_versions(conn)
    assert len(db.list_webpage_versions(user["id"], chat["id"])) == 1


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
            # The tool really runs, so the marker's version id is a stored row.
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
    version_id = events["webpage"][0]["versionId"]
    assert version_id
    assert db.get_webpage_active_version(user["id"], chat["id"]) == version_id
    versions = db.list_webpage_versions(user["id"], chat["id"])
    assert len(versions) == 1
    assert versions[0]["id"] == version_id
    assert versions[0]["messageUid"] == "reply-stream"
    # The reply is finished: the chat is no longer busy.
    assert chat["id"] not in main._WEBPAGE_BUSY
