"""Chat and account deletion leave no rows, files, site folders or containers.

The container engine is mocked here (sitebox.remove_container records the ids
it was asked to remove); test_sitebox.py checks the real removal against a
live engine.
"""

import pytest

from app import db, google_oauth, sitebox, webpage_git

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"

# Every table holding per-user rows, with the column that ties a row to the user.
USER_TABLES = (
    "sessions",
    "chats",
    "google_grants",
    "oauth_states",
    "sent_drafts",
    "deleted_events",
    "images",
    "attachments",
    "webpage_sites",
    "memories",
)


@pytest.fixture
def removed_containers(monkeypatch):
    removed: list[str] = []

    def fake_remove(site_id: str) -> bool:
        removed.append(site_id)
        return True

    monkeypatch.setattr(sitebox, "remove_container", fake_remove)
    return removed


def _kid():
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    return user


def _fill_chat(user_id: str, chat_id: str) -> dict:
    """Give a chat a message, an image, an attachment and a committed webpage."""
    db.append_chat_message(chat_id, "user", "build me a page")
    image = db.create_image(user_id, chat_id, FAKE_PNG)
    attachment = db.create_attachment(
        user_id, chat_id, "notes.txt", b"hello", kind="text", mime="text/plain", text="hello"
    )
    site = db.get_or_create_webpage_site(user_id, chat_id, f"key-{chat_id}")
    workspace = sitebox.workspace_dir(site["id"])
    (workspace / "index.html").write_text("<!doctype html><title>Hi</title>", encoding="utf-8")
    # A real commit, so the folder holds git's read-only object files.
    assert webpage_git.commit_workspace(site, origin="user", label="first", message_uid=None)
    return {
        "image": db.image_file_path(image["id"], image["mime"]),
        "attachment": db.attachment_file_path(attachment["id"], attachment["filename"]),
        "workspace": workspace,
        "site_id": site["id"],
    }


def _count(table: str, user_id: str) -> int:
    return db.get_conn().execute(
        f"SELECT COUNT(*) FROM {table} WHERE user_id = :uid", {"uid": user_id}
    ).fetchone()[0]


def _messages(chat_id: str) -> int:
    return db.get_conn().execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id = :id", {"id": chat_id}
    ).fetchone()[0]


# ---------- chat deletion ----------


def test_chat_delete_removes_its_webpage_folder_and_container(signed_in, removed_containers):
    user = _kid()
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    kept = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    doomed = _fill_chat(user["id"], chat["id"])
    survivor = _fill_chat(user["id"], kept["id"])
    assert (doomed["workspace"] / ".git").is_dir()

    assert signed_in.delete(f"/api/v1/chats/{chat['id']}").json() == {"ok": True}

    assert db.get_webpage_site(user["id"], chat["id"]) is None
    assert _messages(chat["id"]) == 0
    assert not doomed["image"].exists()
    assert not doomed["attachment"].exists()
    assert not doomed["workspace"].exists()
    assert removed_containers == [doomed["site_id"]]

    # The other chat keeps everything.
    assert db.get_webpage_site(user["id"], kept["id"]) is not None
    assert survivor["image"].is_file()
    assert survivor["attachment"].is_file()
    assert (survivor["workspace"] / "index.html").is_file()


def test_chat_delete_without_a_webpage_touches_no_container(signed_in, removed_containers):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    assert signed_in.delete(f"/api/v1/chats/{chat['id']}").json() == {"ok": True}
    assert removed_containers == []


def test_chat_delete_is_user_scoped(signed_in, removed_containers):
    stranger = db.create_google_user("stranger@example.com", "Stranger", "sub-x")
    chat = db.create_chat(stranger["id"], title="Theirs")
    files = _fill_chat(stranger["id"], chat["id"])

    assert signed_in.delete(f"/api/v1/chats/{chat['id']}").status_code == 404
    assert files["workspace"].is_dir()
    assert removed_containers == []


# ---------- account deletion ----------


def test_delete_account_removes_every_trace(signed_in, removed_containers, monkeypatch):
    user = _kid()
    revoked: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        google_oauth.httpx, "post", lambda url, data=None, **kw: revoked.append((url, data))
    )

    chats = [signed_in.post("/api/v1/chats", json={"mode": "chat"}).json() for _ in range(2)]
    files = [_fill_chat(user["id"], chat["id"]) for chat in chats]
    db.create_memory(user["id"], "Likes ballet")
    google_oauth.store_grant(
        user["id"], "gmail", {"access_token": "gm-at", "refresh_token": "gm-rt", "scope": "g"}
    )
    google_oauth.store_grant(user["id"], "calendar", {"access_token": "cal-at", "scope": "c"})
    db.mark_draft_sent(user["id"], "draft-1")
    db.mark_event_deleted(user["id"], "event-1")
    db.create_oauth_state("gmail", user["id"])

    # Someone else's data must survive.
    other = db.create_google_user("other@example.com", "Other", "sub-other")
    other_chat = db.create_chat(other["id"], title="Mine")
    other_files = _fill_chat(other["id"], other_chat["id"])
    db.create_session(other["id"])

    res = signed_in.delete("/api/v1/auth/me")
    assert res.json() == {"ok": True}
    assert "cappuccina_session" in res.headers["set-cookie"]

    # Google was asked to revoke each grant: the refresh token when there is
    # one, else the access token.
    assert sorted(data["token"] for _, data in revoked) == ["cal-at", "gm-rt"]
    assert {url for url, _ in revoked} == {google_oauth.GOOGLE_REVOKE_URL}

    assert db.get_user_by_id(user["id"]) is None
    for table in USER_TABLES:
        assert _count(table, user["id"]) == 0, table
    for chat, chat_files in zip(chats, files):
        assert _messages(chat["id"]) == 0
        assert not chat_files["image"].exists()
        assert not chat_files["attachment"].exists()
        assert not chat_files["workspace"].exists()
    assert sorted(removed_containers) == sorted(f["site_id"] for f in files)

    # The session is gone too: the cookie no longer signs anyone in.
    assert signed_in.get("/api/v1/auth/me").status_code == 401

    assert db.get_user_by_id(other["id"]) is not None
    assert _count("sessions", other["id"]) == 1
    assert db.get_webpage_site(other["id"], other_chat["id"]) is not None
    assert other_files["image"].is_file()
    assert other_files["attachment"].is_file()
    assert (other_files["workspace"] / "index.html").is_file()


def test_delete_account_survives_a_failed_revoke(signed_in, removed_containers, monkeypatch):
    user = _kid()

    def offline(*args, **kwargs):
        raise google_oauth.httpx.ConnectError("offline")

    monkeypatch.setattr(google_oauth.httpx, "post", offline)
    google_oauth.store_grant(user["id"], "gmail", {"access_token": "at", "refresh_token": "rt"})

    assert signed_in.delete("/api/v1/auth/me").json() == {"ok": True}
    assert db.get_user_by_id(user["id"]) is None
    assert _count("google_grants", user["id"]) == 0


def test_delete_account_requires_sign_in(client):
    assert client.delete("/api/v1/auth/me").status_code == 401


def test_destroy_site_logs_and_still_removes_the_folder_when_the_engine_is_down(
    isolated_db, monkeypatch
):
    monkeypatch.setattr(sitebox, "remove_container", lambda site_id: False)
    site_id = "ab" + "0" * 30
    workspace = sitebox.workspace_dir(site_id)
    (workspace / "index.html").write_text("hi", encoding="utf-8")

    sitebox.destroy_site(site_id)

    assert not workspace.exists()
    assert not workspace.parent.exists()  # the emptied shard bucket goes too
