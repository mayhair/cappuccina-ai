"""Open a chat's webpage in headless Chromium and report what is broken.

Cappuccina writes pages without ever running them, so a script that throws on
its first line or a close button with no handler used to ship as "it's
ready!". The checker loads the page exactly as the canvas does — the same
sandboxed iframe (allow-scripts allow-forms allow-modals, never same-origin)
and the same served bytes from webpage_preview.resolve_preview, preview
runtime included — then:

- collects uncaught errors, console.error calls and files that 404;
- flags elements that carry `hidden` but are still showing (CSS overriding it);
- clicks every visible button-like thing, and the buttons that only appear
  after a click (a popup's close button), and reports clicks that throw,
  reload the page, or change nothing at all;
- optionally runs the model's own steps (click/type/press/expect_*).

The browser never touches the network beyond public https hosts (CDNs, fonts):
the site's own files are answered from disk under a fake origin, and every
http, localhost or private-address request is aborted, so page code cannot
reach this backend or anything else on the host's network.

Chromium comes from Python Playwright (`python -m playwright install
chromium`; its OS libraries on Linux come from `install-deps chromium`);
without it every check raises CheckUnavailable. The real failure is logged
server-side so a misconfigured host is diagnosable.
"""

import asyncio
import ipaddress
import logging
import re
import sys
import time
from urllib.parse import unquote, urlsplit

from app import sitebox
from app.webpage_preview import (
    PREVIEW_HEADERS,
    preview_base,
    resolve_preview,
    serve_root,
)

logger = logging.getLogger(__name__)

CHECK_ORIGIN = "http://site.check"
# Must match the canvas iframe (src/components/WebpageCanvas.jsx).
SANDBOX = "allow-scripts allow-forms allow-modals"
VIEWPORT = {"width": 1280, "height": 800}

TIME_BUDGET = 25.0  # seconds of clicking before the sweep stops
HARD_TIMEOUT = 60.0  # backstop for the whole check, browser start included
LOAD_TIMEOUT_MS = 10_000
ACTION_TIMEOUT_MS = 2_000
SETTLE_MS = 400  # what a click gets to show its effect
MAX_TARGETS = 20
MAX_CHILD_TARGETS = 5
MAX_STEPS = 20
MAX_REPORT_ITEMS = 12

UNAVAILABLE_MESSAGE = (
    "Testing webpages isn't set up on this server (headless Chromium is missing), "
    "so check the page by reading your code carefully instead."
)


class CheckUnavailable(Exception):
    """Playwright or its Chromium is missing or cannot start on this server."""


def _harness(src: str) -> str:
    """The top page: the site in the canvas's own sandboxed iframe."""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        "html,body{margin:0;height:100%}"
        "iframe{border:0;width:100%;height:100%;display:block}</style></head>"
        f"<body><iframe sandbox=\"{SANDBOX}\" src=\"{src}\"></iframe></body></html>"
    )


# Runs in every frame before the page's own scripts. In the site frame it
# records lookups that found nothing, which explains most "Cannot read
# properties of null" errors (a renamed id, a script that runs before the
# element exists).
_INIT_SCRIPT = """
(() => {
  if (window.top === window) return;
  const missed = (window.__capMissed = []);
  const note = (how, arg) => {
    if (typeof arg === 'string' && missed.length < 20) missed.push(`${how}(${JSON.stringify(arg)})`);
  };
  const byId = Document.prototype.getElementById;
  Document.prototype.getElementById = function (id) {
    const found = byId.call(this, id);
    if (found === null) note('getElementById', id);
    return found;
  };
  const one = Document.prototype.querySelector;
  Document.prototype.querySelector = function (selector) {
    const found = one.call(this, selector);
    if (found === null) note('querySelector', selector);
    return found;
  };
})();
"""

# One string per element (keyed by a per-load id) describing how it looks:
# hidden elements are "-", shown ones carry their box, classes, own text,
# colors, transform, form state and, for a canvas, its pixels. Two snapshots
# differ exactly where something visibly changed.
_SNAPSHOT_JS = """
() => {
  const ids = window.__capIds || (window.__capIds = new WeakMap());
  let next = window.__capNext || 0;
  const state = {};
  const all = document.body ? document.body.getElementsByTagName('*') : [];
  for (let i = 0; i < all.length && i < 4000; i++) {
    const el = all[i];
    let id = ids.get(el);
    if (id === undefined) { id = ++next; ids.set(el, id); }
    const r = el.getBoundingClientRect();
    const shown = r.width > 0 && r.height > 0 &&
      el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    if (!shown) { state[id] = '-'; continue; }
    const cs = getComputedStyle(el);
    let own = '';
    for (const n of el.childNodes) if (n.nodeType === 3) own += n.nodeValue;
    let s = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height),
      typeof el.className === 'string' ? el.className : '', own.trim().slice(0, 80),
      cs.color, cs.backgroundColor, cs.transform, cs.opacity,
      el.value === undefined ? '' : String(el.value), el.checked ? 'on' : ''].join('|');
    if (el.tagName === 'CANVAS') {
      try { const d = el.toDataURL(); s += '|' + d.length + d.slice(-48); } catch (e) {}
    }
    state[id] = s;
  }
  window.__capNext = next;
  state['@'] = location.hash + '|' + Math.round(scrollY) + '|' + document.title;
  return state;
}
"""

# Everything a person would try clicking, as a CSS path that finds it again
# after a reload, plus how to describe it. A cursor:pointer element counts
# (a <span class="close">×</span> is a button to a person), but not one that
# only inherited the pointer from a clickable parent.
_TARGETS_JS = """
() => {
  const SEL = 'button, [role=button], [onclick], a[href^="#"], a[href=""], a:not([href]), summary, ' +
    'input[type=button], input[type=submit], input[type=reset], input[type=checkbox], input[type=radio]';
  const POPUP = 'dialog, [role=dialog], [aria-modal], [class*=modal], [class*=popup], [class*=dialog], ' +
    '[class*=overlay], [class*=lightbox], [id*=modal], [id*=popup], [id*=dialog], [id*=overlay]';
  const CLOSE = /^(×|✕|✖|x|close|cancel|ok|okay|got it|dismiss|done|back|no thanks)$/i;
  const path = (el) => {
    const parts = [];
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      let i = 1;
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) i++;
      parts.unshift(`${n.tagName.toLowerCase()}:nth-child(${i})`);
    }
    return 'body > ' + parts.join(' > ');
  };
  const describe = (el) => {
    const text = (el.getAttribute('aria-label') || el.innerText || el.value || el.title ||
      el.getAttribute('alt') || '').trim().replace(/\\s+/g, ' ');
    if (text) return text.slice(0, 40);
    let d = el.tagName.toLowerCase();
    if (el.id) d += '#' + el.id;
    else if (typeof el.className === 'string' && el.className.trim()) d += '.' + el.className.trim().split(/\\s+/)[0];
    return d;
  };
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  };
  const out = [];
  const picked = new Set();
  const all = document.body ? document.body.getElementsByTagName('*') : [];
  for (let i = 0; i < all.length && i < 4000; i++) {
    const el = all[i];
    if (el.disabled || el.closest('[inert]')) continue;
    if (el.tagName === 'A' && el.getAttribute('href') && !el.getAttribute('href').startsWith('#')) continue;
    let clickable = el.matches(SEL);
    if (!clickable) {
      const parent = el.parentElement;
      clickable = getComputedStyle(el).cursor === 'pointer' &&
        !(parent && getComputedStyle(parent).cursor === 'pointer');
    }
    if (!clickable || !shown(el)) continue;
    let inside = false;
    for (let p = el.parentElement; p; p = p.parentElement) if (picked.has(p)) { inside = true; break; }
    if (inside) continue;
    picked.add(el);
    const label = describe(el);
    const popupEl = el.closest(POPUP);
    out.push({
      path: path(el),
      label,
      popup: popupEl ? describe(popupEl) : '',
      close: CLOSE.test(label) || /close|dismiss/i.test(el.getAttribute('aria-label') || '') ||
        /close|dismiss/i.test(typeof el.className === 'string' ? el.className : ''),
      inForm: Boolean(el.form || el.closest('form')) && el.type !== 'button',
    });
  }
  return out;
}
"""

# Elements that say `hidden` but show anyway: CSS such as `.modal{display:flex}`
# beats the attribute, so code that sets `el.hidden = true` to close a popup
# leaves it on screen.
_HIDDEN_SHOWN_JS = """
() => [...document.querySelectorAll('[hidden]')]
  .filter((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.checkVisibility();
  })
  .slice(0, 5)
  .map((el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
    (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\\s+/)[0] : ''))
"""

_EMPTY_JS = """
() => !document.body || (!document.body.innerText.trim() &&
  !document.querySelector('img, canvas, svg, video, iframe, input, button'))
"""

_CONSOLE_NOISE = ("Failed to load resource",)

# The keys a scripted step may use, in the order they run within one step.
_STEP_KEYS = ("click", "type", "press", "wait", "expect_visible", "expect_hidden", "expect_text")


def _public_https(url: str) -> bool:
    """True for https URLs on public hosts — the only network a page may use."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or "." not in host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def _changed(before: dict, after: dict, ignore: set) -> bool:
    """Whether anything outside the always-moving elements looks different."""
    for key in before.keys() | after.keys():
        if key in ignore:
            continue
        if before.get(key) != after.get(key):
            return True
    return False


def _moving(a: dict, b: dict) -> set:
    """Elements that changed between two idle snapshots (animations, clocks)."""
    return {key for key in a.keys() | b.keys() if a.get(key) != b.get(key)}


def _short_error(exc: Exception) -> str:
    """A Playwright error's first line, without its call log."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return re.sub(r"^\w+\.\w+: ", "", text)[:200]


# Where in the site an error was thrown: "app.js:12" out of a stack frame URL.
_STACK_SITE_FILE = re.compile(r"/preview/[^/\s]+/([^\s:)]+):(\d+):\d+")


def _page_error_text(error) -> str:
    """An uncaught error as "TypeError: x is null (app.js:3)".

    Read from Playwright's pageerror, which carries the real message even
    where the page's own error event only sees a muted "Script error."
    """
    name = getattr(error, "name", "") or "Error"
    message = (getattr(error, "message", "") or str(error)).strip()[:300]
    where = _STACK_SITE_FILE.search(getattr(error, "stack", "") or "")
    text = message if message.startswith(name) else f"{name}: {message}"
    return f"{text} ({where.group(1)}:{where.group(2)})" if where else text


class _Run:
    """One check: the browser page, what it has seen so far, and the findings."""

    def __init__(self, page, workspace, base):
        self.page = page
        self.workspace = workspace
        self.base = base
        self.frame = None
        self.moving: set = set()
        self.errors: list[str] = []
        self.console: list[str] = []
        self.dialogs: list[str] = []
        self.missing: list[str] = []
        self.blocked: list[str] = []
        self.problems: list[str] = []
        self.notes: list[str] = []
        self.clicked = 0
        self.seen_hidden: set[str] = set()

    # -- the page --

    async def route(self, route):
        url = route.request.url
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" == CHECK_ORIGIN:
            path = unquote(parts.path)
            if path == "/":
                await route.fulfill(
                    body=_harness(self.base + "index.html"), content_type="text/html"
                )
                return
            if path.startswith(self.base):
                relative = path[len(self.base) :]
                served = resolve_preview(self.workspace, self.base, relative)
                if served is not None:
                    body, mime = served
                    await route.fulfill(body=body, content_type=mime, headers=PREVIEW_HEADERS)
                    return
                if relative not in self.missing:
                    self.missing.append(relative)
            await route.fulfill(status=404, body="Not found", headers=PREVIEW_HEADERS)
            return
        if _public_https(url):
            await route.continue_()
            return
        origin = f"{parts.scheme}://{parts.netloc}" if parts.netloc else url[:60]
        if origin not in self.blocked:
            self.blocked.append(origin)
        await route.abort()

    def on_pageerror(self, error):
        self.errors.append(_page_error_text(error))

    def on_console(self, message):
        if message.type != "error":
            return
        text = message.text.strip()
        if not text or text.startswith(_CONSOLE_NOISE) or text in self.console:
            return
        self.console.append(text[:300])

    async def on_dialog(self, dialog):
        self.dialogs.append(f"{dialog.type}: {dialog.message[:120]}")
        try:
            await dialog.accept()
        except Exception:
            pass

    async def load(self) -> bool:
        """A fresh copy of the page; False when the site frame never appeared."""
        try:
            await self.page.goto(CHECK_ORIGIN + "/", wait_until="load", timeout=LOAD_TIMEOUT_MS)
        except Exception:
            # A CDN that never answers holds `load` back; judge what did load.
            pass
        # Right after a reload the old, detached frame can still be listed.
        frames = [f for f in self.page.main_frame.child_frames if not f.is_detached()]
        if not frames:
            return False
        self.frame = frames[0]
        await self.page.wait_for_timeout(SETTLE_MS)
        first = await self.snapshot()
        await self.page.wait_for_timeout(300)
        self.moving = _moving(first, await self.snapshot())
        return True

    async def snapshot(self) -> dict:
        try:
            return await self.frame.evaluate(_SNAPSHOT_JS)
        except Exception:
            return {}

    async def targets(self) -> list[dict]:
        try:
            return await self.frame.evaluate(_TARGETS_JS)
        except Exception:
            return []

    async def check_hidden_shown(self) -> None:
        try:
            found = await self.frame.evaluate(_HIDDEN_SHOWN_JS)
        except Exception:
            return
        for name in found:
            if name in self.seen_hidden:
                continue
            self.seen_hidden.add(name)
            self.problems.append(
                f"{name} has the hidden attribute but is still showing: your CSS gives it "
                "a display value (like display:flex) that beats [hidden]. Add "
                "`[hidden] { display: none !important; }` to the CSS, or toggle a class instead."
            )

    # -- the checks --

    async def check_load(self) -> None:
        for text in dict.fromkeys(self.errors):
            self.problems.append(f"On load the page threw: {text}")
        for text in self.console:
            self.problems.append(f"On load the console logged an error: {text}")
        await self.check_hidden_shown()
        try:
            empty = await self.frame.evaluate(_EMPTY_JS)
        except Exception:
            empty = False
        if empty:
            self.problems.append("The page shows nothing at all (an empty body).")
        try:
            missed = await self.frame.evaluate("window.__capMissed || []")
        except Exception:
            missed = []
        if missed and (self.errors or self.console):
            self.notes.append(
                "Lookups that found no element: " + ", ".join(missed[:6])
                + " (an id that doesn't match the HTML, or a script running before the element exists)."
            )

    def finish(self) -> dict:
        """The findings, with the files and hosts the whole visit asked for."""
        missing = [
            f"The page asks for `{path}`, but there is no such file in the site "
            "(check the file name and the relative path)."
            for path in self.missing
            if path not in ("", "index.html")
        ]
        blocked = [
            f"The page tried to reach {origin}; only https URLs on public hosts load."
            for origin in self.blocked
        ]
        return {
            "problems": missing + self.problems,
            "notes": blocked + self.notes,
            "clicked": self.clicked,
        }

    async def click(self, target: dict, setup: list[dict]) -> tuple[bool, list[dict]]:
        """Click one target and judge what happened.

        `setup` are the clicks that reveal it (a popup's opener). Returns
        (whether the page changed, the targets that appeared afterwards).
        """
        before_targets = {t["path"] for t in await self.targets()} if not setup else set()
        before = await self.snapshot()
        errors_before = len(self.errors)
        console_before = len(self.console)
        dialogs_before = len(self.dialogs)
        try:
            await self.frame.evaluate("window.__capMark = 1")
        except Exception:
            pass
        name = f'"{target["label"]}"'
        if target.get("popup"):
            name += f' (inside {target["popup"]})'
        self.clicked += 1
        try:
            await self.frame.locator(target["path"]).first.click(timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            text = _short_error(exc)
            covered = re.search(r"(<[^>]+>)[^<]*intercepts pointer events", str(exc))
            if covered:
                self.notes.append(
                    f"{name} can't be clicked: {covered.group(1)[:80]} sits on top of it. "
                    "Fine if that's a popup that is meant to be open; otherwise it is an "
                    "invisible layer blocking clicks."
                )
            else:
                self.notes.append(f"Couldn't click {name}: {text}")
            return False, []
        await self.page.wait_for_timeout(SETTLE_MS)
        try:
            same_page = await self.frame.evaluate("window.__capMark === 1")
        except Exception:
            same_page = False
        errors = self.errors[errors_before:] + self.console[console_before:]
        if errors:
            self.problems.append(f"Clicking {name} threw: {errors[0]}")
        if not same_page:
            if target.get("inForm"):
                self.problems.append(
                    f"Clicking {name} reloaded the page: it submits its <form>. Give the "
                    "button type=\"button\", or call event.preventDefault() in the submit handler."
                )
            else:
                self.problems.append(f"Clicking {name} reloaded or left the page.")
            return True, []
        await self.check_hidden_shown()
        after = await self.snapshot()
        changed = _changed(before, after, self.moving) or len(self.dialogs) > dialogs_before
        if not changed and not errors:
            closing = target.get("close") or target.get("popup")
            if not self.moving or closing:
                what = "the popup stays open" if target.get("popup") else "no visible change"
                self.problems.append(
                    f"Clicking {name} did nothing ({what}). Check that a click handler is "
                    "attached to it and that it changes what it should."
                )
            else:
                self.notes.append(
                    f"Clicking {name} changed nothing visible outside the animated parts "
                    "(fine if it only plays a sound or affects the animation)."
                )
        if setup or not changed:
            return changed, []
        appeared = [t for t in await self.targets() if t["path"] not in before_targets]
        return changed, appeared

    async def sweep(self, deadline: float) -> None:
        targets = (await self.targets())[:MAX_TARGETS]
        dirty = False
        for index, target in enumerate(targets):
            if time.monotonic() > deadline:
                self.notes.append(
                    f"Stopped clicking after {self.clicked} clicks (time limit); "
                    f"{len(targets) - index} buttons weren't tried."
                )
                return
            if dirty and not await self.load():
                return
            changed, appeared = await self.click(target, [])
            dirty = changed
            ready = True  # the page is in the state right after `target`
            for child in appeared[:MAX_CHILD_TARGETS]:
                if time.monotonic() > deadline:
                    break
                if not ready:
                    if not await self.load():
                        return
                    try:
                        await self.frame.locator(target["path"]).first.click(
                            timeout=ACTION_TIMEOUT_MS
                        )
                    except Exception:
                        break
                    await self.page.wait_for_timeout(SETTLE_MS)
                child_changed, _ = await self.click(child, [target])
                ready = not child_changed
                dirty = True

    async def run_steps(self, steps: list) -> None:
        for number, step in enumerate(steps[:MAX_STEPS], start=1):
            if not isinstance(step, dict):
                self.problems.append(f"Step {number} isn't an object like {{\"click\": \"#open\"}}.")
                return
            errors_before = len(self.errors)
            console_before = len(self.console)
            try:
                failure = await self._step(step)
            except Exception as exc:
                failure = _short_error(exc)
            label = ", ".join(f"{k}={v!r}" for k, v in step.items())[:120]
            if failure:
                self.problems.append(f"Step {number} ({label}) failed: {failure}")
                return
            errors = self.errors[errors_before:] + self.console[console_before:]
            if errors:
                self.problems.append(f"Step {number} ({label}) threw: {errors[0]}")
                return

    async def _step(self, step: dict) -> str | None:
        """Run one step; a string says why it failed.

        A step may combine an action with expectations ({"click": "#open",
        "expect_visible": "#popup"}): its keys run in the order of _STEP_KEYS,
        actions first.
        """
        unknown = set(step) - set(_STEP_KEYS) - {"text", "selector"}
        if unknown or not set(step) & set(_STEP_KEYS):
            return (
                "unknown step; use click, type (+text), press (+selector), wait, "
                "expect_visible, expect_hidden or expect_text (+text)"
            )
        frame = self.frame
        text = str(step.get("text", ""))
        for key in _STEP_KEYS:
            if key not in step:
                continue
            value = step[key]
            if key == "click":
                await frame.locator(str(value)).first.click(timeout=ACTION_TIMEOUT_MS)
            elif key == "type":
                await frame.locator(str(value)).first.fill(text, timeout=ACTION_TIMEOUT_MS)
            elif key == "press":
                target = str(step.get("selector") or "body")
                await frame.locator(target).first.press(str(value), timeout=ACTION_TIMEOUT_MS)
            elif key == "wait":
                await self.page.wait_for_timeout(max(0, min(3000, int(value))))
                continue
            elif key == "expect_visible":
                try:
                    await frame.locator(str(value)).first.wait_for(
                        state="visible", timeout=ACTION_TIMEOUT_MS
                    )
                except Exception:
                    return f"expected {value} to be visible, but it isn't"
                continue
            elif key == "expect_hidden":
                try:
                    await frame.locator(str(value)).first.wait_for(
                        state="hidden", timeout=ACTION_TIMEOUT_MS
                    )
                except Exception:
                    return f"expected {value} to be hidden, but it is still showing"
                continue
            elif key == "expect_text":
                shown = await frame.locator(str(value)).first.inner_text(timeout=ACTION_TIMEOUT_MS)
                if text not in shown:
                    return f"expected {value} to contain {text!r}, but it shows {shown[:80]!r}"
                continue
            # After an action, give the page a moment to react.
            await self.page.wait_for_timeout(300)
        return None


async def _check(workspace, base: str, steps: list | None) -> dict:
    from playwright.async_api import async_playwright

    started = time.monotonic()
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception as exc:
            # The message for the model stays friendly; the real cause (a
            # missing OS library, a half-downloaded browser, …) goes to the
            # server log, where the Playwright error carries the browser's
            # own stderr.
            logger.warning("check_webpage: launching Chromium failed: %s", exc)
            raise CheckUnavailable(UNAVAILABLE_MESSAGE) from exc
        try:
            context = await browser.new_context(viewport=VIEWPORT, service_workers="block")
            await context.add_init_script(_INIT_SCRIPT)
            page = await context.new_page()
            run = _Run(page, workspace, base)
            await context.route("**/*", run.route)
            page.on("pageerror", run.on_pageerror)
            page.on("console", run.on_console)
            page.on("dialog", run.on_dialog)
            if not (serve_root(workspace) / "index.html").is_file():
                return {
                    "problems": ["The site has no index.html yet, so there is no page to show."],
                    "notes": [],
                    "clicked": 0,
                }
            if not await run.load():
                run.problems.append("The page didn't load at all.")
                return run.finish()
            await run.check_load()
            deadline = started + TIME_BUDGET
            if steps:
                await run.run_steps(steps)
                if not await run.load():
                    return run.finish()
            await run.sweep(deadline)
            return run.finish()
        finally:
            await browser.close()


def run_check(site: dict, steps: list | None = None) -> dict:
    """Check a site's page; {"problems": [...], "notes": [...], "clicked": n}.

    Blocking (it drives its own event loop), so call it from a worker thread —
    tools and graph nodes already run there. Raises CheckUnavailable when
    Playwright or Chromium is missing or cannot start.
    """
    try:
        import playwright.async_api  # noqa: F401
    except ImportError as exc:
        logger.warning("check_webpage: Playwright is not importable: %s", exc)
        raise CheckUnavailable(UNAVAILABLE_MESSAGE) from exc
    workspace = sitebox.workspace_dir(site["id"])
    base = preview_base(site)
    # Playwright starts Chromium as a subprocess, which on Windows needs a
    # proactor loop; uvicorn --reload installs the selector policy globally,
    # so the loop is built explicitly rather than taken from the policy.
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            asyncio.wait_for(_check(workspace, base, steps), HARD_TIMEOUT)
        )
    except asyncio.TimeoutError:
        return {
            "problems": [],
            "notes": ["The check took too long and was stopped before it finished."],
            "clicked": 0,
        }
    finally:
        loop.close()


def format_report(result: dict) -> str:
    """The check as plain text for the model: problems first, then notes."""
    problems = result.get("problems") or []
    notes = result.get("notes") or []
    clicked = result.get("clicked", 0)
    lines = [f"Opened the page in a headless browser and clicked {clicked} thing(s)."]
    if problems:
        lines.append(f"Problems ({len(problems)}), fix these before telling the user it's ready:")
        lines += [f"{n}. {p}" for n, p in enumerate(problems[:MAX_REPORT_ITEMS], start=1)]
        if len(problems) > MAX_REPORT_ITEMS:
            lines.append(f"…and {len(problems) - MAX_REPORT_ITEMS} more.")
    else:
        lines.append("No problems found: no errors, no missing files, and every click did something.")
    if notes:
        lines.append("Notes (might be fine):")
        lines += [f"- {note}" for note in notes[:6]]
    return "\n".join(lines)
