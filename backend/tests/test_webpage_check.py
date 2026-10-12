"""The headless page checker against small sites with known bugs.

Skipped when Playwright's Chromium is not installed
(`python -m playwright install chromium`).
"""

import pytest

from app import db, sitebox, webpage_check


def _chromium_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(not _chromium_ready(), reason="Chromium for Playwright is not installed")


@pytest.fixture
def site(isolated_db):
    user = db.create_google_user("check@example.com", "Checker", "google-sub-check")
    chat = db.create_chat(user["id"], title="Check")
    return db.create_webpage_site(user["id"], chat["id"], "check-key")


def _write(site, files: dict) -> None:
    workspace = sitebox.workspace_dir(site["id"])
    for name, text in files.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _page(body: str, script: str = "", css: str = "") -> dict:
    return {
        "index.html": (
            "<!doctype html><html><head><meta charset=\"utf-8\"><title>T</title>"
            "<link rel=\"stylesheet\" href=\"styles.css\"></head><body>"
            f"{body}<script src=\"script.js\"></script></body></html>"
        ),
        "styles.css": css or "body { font-family: sans-serif; }",
        "script.js": script or "// nothing",
    }


MODAL_BODY = (
    '<button id="open">Open</button>'
    '<div id="modal" class="modal" hidden><p>Hi!</p><button id="close">×</button></div>'
)
MODAL_CSS = ".modal { position: fixed; inset: 20%; background: #fff; border: 1px solid; }"


def test_a_working_popup_is_clean(site):
    _write(
        site,
        _page(
            MODAL_BODY,
            "const m = document.getElementById('modal');"
            "document.getElementById('open').onclick = () => { m.hidden = false; };"
            "document.getElementById('close').onclick = () => { m.hidden = true; };",
            MODAL_CSS,
        ),
    )
    result = webpage_check.run_check(site)
    assert result["problems"] == []
    # Open, then the close button that only exists once the popup is open.
    assert result["clicked"] >= 2


def test_a_dead_close_button_is_reported(site):
    _write(
        site,
        _page(
            MODAL_BODY,
            "document.getElementById('open').onclick = () => {"
            " document.getElementById('modal').hidden = false; };",
            MODAL_CSS,
        ),
    )
    result = webpage_check.run_check(site)
    assert any('"×"' in p and "did nothing" in p for p in result["problems"]), result


def test_css_beating_the_hidden_attribute_is_explained(site):
    _write(
        site,
        _page(
            MODAL_BODY,
            "const m = document.getElementById('modal');"
            "document.getElementById('open').onclick = () => { m.hidden = false; };"
            "document.getElementById('close').onclick = () => { m.hidden = true; };",
            MODAL_CSS + " .modal { display: flex; }",
        ),
    )
    result = webpage_check.run_check(site)
    assert any("hidden attribute" in p for p in result["problems"]), result


def test_local_storage_works_in_the_sandbox(site):
    # The canvas origin is opaque, so without the preview runtime's shim this
    # first line would throw and leave the button without a handler.
    _write(
        site,
        _page(
            '<button id="b">Count</button><span id="n">0</span>',
            "let best = Number(localStorage.getItem('best') || 0);"
            "document.getElementById('b').onclick = () => {"
            " best += 1; localStorage.setItem('best', best);"
            " document.getElementById('n').textContent = best; };",
        ),
    )
    result = webpage_check.run_check(site)
    assert result["problems"] == []


def test_errors_and_missing_files_are_reported(site):
    files = _page('<button id="go">Go</button><img src="img/cat.png" alt="cat">', "")
    files["index.html"] = files["index.html"].replace("script.js", "scripts.js")
    files["app.js"] = "document.getElementById('nope').addEventListener('click', () => {});"
    files["index.html"] = files["index.html"].replace(
        "</body>", '<script src="app.js"></script></body>'
    )
    _write(site, files)
    result = webpage_check.run_check(site)
    problems = "\n".join(result["problems"])
    assert "`scripts.js`" in problems
    assert "`img/cat.png`" in problems
    assert "On load the page threw" in problems and "app.js" in problems


def test_a_form_button_that_reloads_the_page_is_reported(site):
    _write(
        site,
        _page('<form><input name="q"><button>Add</button></form><ul id="list"></ul>'),
    )
    result = webpage_check.run_check(site)
    assert any("reloaded the page" in p for p in result["problems"]), result


def test_the_page_cannot_reach_local_addresses(site):
    _write(
        site,
        _page(
            '<p id="out">waiting</p>',
            "fetch('http://127.0.0.1:8000/api/v1/me').then(() => {"
            " document.getElementById('out').textContent = 'reached'; })"
            ".catch(() => { document.getElementById('out').textContent = 'blocked'; });",
        ),
    )
    result = webpage_check.run_check(site)
    assert any("127.0.0.1:8000" in note for note in result["notes"]), result


def test_steps_check_a_flow(site):
    _write(
        site,
        _page(
            MODAL_BODY,
            "document.getElementById('open').onclick = () => {"
            " document.getElementById('modal').hidden = false; };",
            MODAL_CSS,
        ),
    )
    result = webpage_check.run_check(
        site,
        [{"click": "#open"}, {"expect_visible": "#modal"}, {"click": "#close"}, {"expect_hidden": "#modal"}],
    )
    assert any("Step 4" in p and "still showing" in p for p in result["problems"]), result
    # An action and its expectation may share one step.
    combined = webpage_check.run_check(
        site, [{"click": "#open", "expect_visible": "#modal"}, {"click": "#close", "expect_hidden": "#modal"}]
    )
    assert any("Step 2" in p and "still showing" in p for p in combined["problems"]), combined


def test_a_site_without_an_index(site):
    _write(site, {"notes.txt": "hi"})
    result = webpage_check.run_check(site)
    assert "no index.html" in result["problems"][0]


def test_the_report_leads_with_problems():
    text = webpage_check.format_report(
        {"problems": ["Clicking \"×\" did nothing."], "notes": ["a note"], "clicked": 3}
    )
    assert text.splitlines()[1].startswith("Problems (1)")
    assert "a note" in text
    clean = webpage_check.format_report({"problems": [], "notes": [], "clicked": 2})
    assert "No problems found" in clean


def test_every_button_is_clicked_after_reloads(site):
    # Each click changes the page, so the sweep reloads between them; the
    # reloaded page's frame (not the detached old one) has to be used.
    _write(
        site,
        _page(
            '<button id="a">A</button><button id="b">B</button><button id="c">C</button>'
            '<p id="out"></p>',
            "for (const id of ['a', 'b', 'c']) document.getElementById(id).onclick = () => {"
            " document.getElementById('out').textContent = id; };",
        ),
    )
    result = webpage_check.run_check(site)
    assert result["clicked"] == 3
    assert result["problems"] == [] and result["notes"] == [], result
    # The sweep still runs after the model's own steps.
    stepped = webpage_check.run_check(site, [{"click": "#a"}, {"expect_text": "#out", "text": "a"}])
    assert stepped["clicked"] == 3 and stepped["problems"] == [], stepped
