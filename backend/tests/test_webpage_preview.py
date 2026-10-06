"""The capability-URL preview route (no session) and one-file reads."""

from app import db, sitebox

KEY = "preview-key-1"
ROOT_HTML = "<!doctype html><title>Root</title><h1>root page</h1>"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00" + b"p" * 200


def _user_and_chat(email="preview@example.com", sub="google-sub-preview"):
    user = db.create_google_user(email, "Previewer", sub)
    chat = db.create_chat(user["id"], title="Preview")
    return user, chat


def _site(user, chat, key=KEY):
    return db.create_webpage_site(user["id"], chat["id"], key)


def _base(chat_id, key=KEY):
    return f"/api/v1/webpages/{chat_id}/preview/{key}/"


def _dist_site():
    """A site whose build output is already in place.

    The page mixes every URL shape the rebasing pass has to tell apart:
    root-absolute (rebased), already-prefixed (left alone), relative and
    protocol-relative (left alone).
    """
    user, chat = _user_and_chat()
    site = _site(user, chat)
    base = _base(chat["id"])
    workspace = sitebox.workspace_dir(site["id"])
    dist = workspace / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>Dist</title>"
        '<link rel="stylesheet" href="/styles.css">'
        f'<link rel="stylesheet" href="{base}already.css">'
        '<script src="/app.js"></script>'
        '<img src="cat.png">'
        '<link rel="stylesheet" href="//cdn.example.com/x.css">',
        encoding="utf-8",
    )
    (dist / "styles.css").write_text("body { color: red; }", encoding="utf-8")
    (dist / "already.css").write_text(".a { color: blue; }", encoding="utf-8")
    (dist / "app.js").write_text("console.log('hi')", encoding="utf-8")
    # Outside the serve root: a traversal must never be able to reach it.
    (workspace / "secret.txt").write_text("secret data", encoding="utf-8")
    return user, chat, site, base, workspace


def _root_site(content=ROOT_HTML):
    """A site with no build output: the workspace root is the serve root."""
    user, chat = _user_and_chat(email="root@example.com", sub="google-sub-root")
    site = _site(user, chat)
    workspace = sitebox.workspace_dir(site["id"])
    (workspace / "index.html").write_text(content, encoding="utf-8")
    return user, chat, site, _base(chat["id"]), workspace


def _assert_preview_headers(res):
    assert res.headers["access-control-allow-origin"] == "*"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["cache-control"] == "no-cache"


# ---------- serving the build output ----------


def test_serves_the_dist_index_with_rebased_urls(client):
    _user, _chat, _site_row, base, _workspace = _dist_site()

    res = client.get(base)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    _assert_preview_headers(res)

    body = res.text
    # Root-absolute URLs are rebased onto the preview base ...
    assert f'href="{base}styles.css"' in body
    assert f'src="{base}app.js"' in body
    # ... while an already-prefixed one is not double-prefixed.
    assert f'href="{base}already.css"' in body
    assert f'href="{base}{base}already.css"' not in body
    assert body.count(f'href="{base}') == 2
    # Relative and protocol-relative URLs pass through byte-identical.
    assert 'src="cat.png"' in body
    assert 'href="//cdn.example.com/x.css"' in body


def test_serves_files_with_script_and_style_mimes(client):
    _user, _chat, _site_row, base, _workspace = _dist_site()

    css = client.get(base + "styles.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    _assert_preview_headers(css)

    js = client.get(base + "app.js")
    assert js.status_code == 200
    assert js.headers["content-type"].startswith("text/javascript")

    assert client.get(base + "cat.png").status_code == 404


def test_extensionless_misses_fall_back_to_the_index(client):
    _user, _chat, _site_row, base, _workspace = _dist_site()

    # A client-side route the build never produced still renders the shell.
    res = client.get(base + "some/deep/page")
    assert res.status_code == 200
    assert "<title>Dist</title>" in res.text

    # A miss that looks like a file is a plain 404 when an index exists.
    assert client.get(base + "missing.png").status_code == 404


def test_a_wrong_or_unknown_key_is_404(client):
    _user, _chat, _site_row, base, _workspace = _dist_site()

    wrong = client.get(_base(_chat["id"], key="not-the-key"))
    assert wrong.status_code == 404
    _assert_preview_headers(wrong)

    assert client.get(f"/api/v1/webpages/nope/preview/{KEY}/").status_code == 404


def test_traversal_and_git_paths_are_blocked(client):
    _user, _chat, _site_row, base, _workspace = _dist_site()

    git = client.get(base + ".git/config")
    assert git.status_code == 404

    for attempt in (
        base + "../secret.txt",
        base + "%2e%2e/secret.txt",
        base + "..%2Fsecret.txt",
        base + "sub/%2e%2e/%2e%2e/secret.txt",
    ):
        res = client.get(attempt)
        assert res.status_code == 404, attempt
        assert "secret" not in res.text


def test_without_a_dist_the_workspace_root_is_served(client):
    _user, _chat, _site_row, base, workspace = _root_site()
    (workspace / "style.css").write_text("h1 { color: green; }", encoding="utf-8")

    res = client.get(base)
    assert res.status_code == 200
    assert "root page" in res.text

    css = client.get(base + "style.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")


def test_a_site_with_no_index_shows_a_placeholder(client):
    user, chat = _user_and_chat(email="empty@example.com", sub="sub-empty")
    site = _site(user, chat)
    workspace = sitebox.workspace_dir(site["id"])
    (workspace / "notes.txt").write_text("just notes", encoding="utf-8")
    base = _base(chat["id"])

    res = client.get(base)
    assert res.status_code == 200
    assert "This site has no index.html yet." in res.text
    _assert_preview_headers(res)

    # A real file is still served from the same root.
    notes = client.get(base + "notes.txt")
    assert notes.status_code == 200
    assert notes.text == "just notes"


# ---------- one-file reads for the Code tab ----------


def test_the_files_route_reads_text_and_describes_binary(signed_in):
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    site = _site(user, chat, key="files-key")
    workspace = sitebox.workspace_dir(site["id"])
    (workspace / "index.html").write_text(ROOT_HTML, encoding="utf-8")
    (workspace / "logo.png").write_bytes(PNG)

    text = signed_in.get(f"/api/v1/webpages/{chat['id']}/files/index.html")
    assert text.status_code == 200
    assert text.json() == {"path": "index.html", "content": ROOT_HTML}

    binary = signed_in.get(f"/api/v1/webpages/{chat['id']}/files/logo.png")
    assert binary.status_code == 200
    assert binary.json() == {
        "path": "logo.png",
        "binary": True,
        "mime": "image/png",
        "size": len(PNG),
    }

    missing = signed_in.get(f"/api/v1/webpages/{chat['id']}/files/nope.txt")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "File not found"

    invalid = signed_in.get(f"/api/v1/webpages/{chat['id']}/files/.git/config")
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "That file name isn't allowed."


def test_the_files_route_needs_a_site(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    res = signed_in.get(f"/api/v1/webpages/{chat['id']}/files/index.html")
    assert res.status_code == 404
    assert res.json()["detail"] == "No webpage yet"
