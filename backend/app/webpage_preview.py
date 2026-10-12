"""What the canvas preview serves for one site path.

Shared by the preview route (app.main) and the page checker
(app.webpage_check), so the checker's headless browser sees exactly the bytes
the user's canvas does — rebased URLs, the placeholder and the preview runtime
included.
"""

import mimetypes
import os
import re
from pathlib import Path

# A page's own assets are fetched by the sandboxed iframe, whose origin is
# opaque: no usable cookie, and module scripts/fetch() are CORS requests with
# `Origin: null`. So every preview response is public, uncacheable and free of
# referrer leakage (the key is what keeps the URL unguessable).
PREVIEW_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-cache",
}

# Shown when a site has been created but has nothing to serve yet (no root
# index.html and no build output), so the canvas iframe shows words instead of
# a bare 404 or a blank document.
PREVIEW_PLACEHOLDER = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<title>Preview</title></head><body>"
    "<p>This site has no index.html yet.</p></body></html>"
)

# mimetypes guesses text/plain (or nothing) for a module script, and the
# browser refuses a script served with the wrong type — so these are pinned.
WEBPAGE_MIME_OVERRIDES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".wasm": "application/wasm",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
}

# The postMessage type the preview runtime reports page errors with; the
# canvas listens for it.
PREVIEW_ERROR_TYPE = "cappuccina-preview-error"

# Injected as the first thing in every served html page. The canvas iframe is
# sandboxed without allow-same-origin, so the page's origin is opaque and
# touching localStorage/sessionStorage throws a SecurityError — which kills a
# script that reads a saved high score on its first line, and with it every
# button the script would have wired up. The shim swaps in in-memory storage
# only when the real one throws, so the canvas behaves like an ordinary tab.
# Uncaught errors and failed loads are relayed to the parent (the canvas shows
# them; the checker collects them). The workspace files are never changed.
PREVIEW_RUNTIME = (
    "<script>(function(){"
    "function memory(){var d=new Map();return{"
    "get length(){return d.size},"
    "key:function(i){var k=Array.from(d.keys())[i];return k===undefined?null:k},"
    "getItem:function(k){k=String(k);return d.has(k)?d.get(k):null},"
    "setItem:function(k,v){d.set(String(k),String(v))},"
    "removeItem:function(k){d.delete(String(k))},"
    "clear:function(){d.clear()}}}"
    "['localStorage','sessionStorage'].forEach(function(n){"
    "try{window[n].length}catch(e){"
    "try{Object.defineProperty(window,n,{value:memory(),configurable:true})}catch(_){}}});"
    "function where(u){return String(u||'').replace(/^.*?\\/preview\\/[^/]+\\//,'')"
    ".replace(/^https?:\\/\\/site\\.check\\//,'')}"
    "function send(d){d.type='" + PREVIEW_ERROR_TYPE + "';"
    "try{parent.postMessage(d,'*')}catch(_){}}"
    "addEventListener('error',function(e){var t=e.target;"
    "if(t&&t!==window&&t.getAttribute){"
    "var u=t.getAttribute('src')||t.getAttribute('href');"
    "if(u)send({kind:'load',message:'Could not load '+u});return}"
    "send({kind:'error',message:e.message||String(e.error),"
    "source:where(e.filename),line:e.lineno||0})},true);"
    "addEventListener('unhandledrejection',function(e){var r=e.reason;"
    "send({kind:'error',message:'Unhandled promise rejection: '+"
    "(r&&r.message?r.message:String(r))})});"
    "})();</script>"
)

_HEAD_OPEN = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_DOCTYPE = re.compile(r"^\s*<!doctype[^>]*>", re.IGNORECASE)
_SCRIPT_TAG = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
_SCRIPT_SRC = re.compile(r"""\bsrc\s*=\s*["']?([^"'\s>]*)""", re.IGNORECASE)
_NOT_LOCAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)

# Root-absolute URLs in a page's own html/css are rebased onto the preview URL:
# a hand-written <link href="/styles.css"> (or a build whose `base` was not
# configured) would otherwise resolve against the backend's root. Relative and
# protocol-relative (//) values, and data: URIs, never match.
_ROOT_ABSOLUTE_URL = re.compile(
    r"""((?:href|src|action)\s*=\s*)(["']?)(/(?!/)[^"'\s>]*)""", re.IGNORECASE
)
_CSS_URL = re.compile(r"""(url\(\s*)(["']?)(/(?!/)[^"')]*)""", re.IGNORECASE)


def preview_base(site: dict) -> str:
    """The root-absolute URL prefix a site's page is served from.

    A capability URL: `preview_key` is unguessable and the preview route
    carries no session, which is what lets the sandboxed canvas iframe load
    module scripts and fetch() a page's own assets.
    """
    return f"/api/v1/webpages/{site['chat_id']}/preview/{site['preview_key']}/"


def webpage_mime(path: str) -> str:
    """Content type for a served site file, with the overrides above."""
    override = WEBPAGE_MIME_OVERRIDES.get(os.path.splitext(path)[1].lower())
    if override:
        return override
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def blocked_segment(path: str) -> bool:
    """True when a path walks into git's or npm's own directories."""
    return any(
        segment in (".git", "node_modules") for segment in path.replace("\\", "/").split("/")
    )


def resolve_under(root: Path, relative: str) -> Path | None:
    """The absolute path of `relative` inside `root`, or None when it escapes.

    normpath first, so a path like a/../../b is judged on its real shape, then
    a resolved-prefix test. Shared by the file routes and the preview route so
    neither can read or write outside the workspace it belongs to.
    """
    resolved = Path(os.path.normpath(os.path.join(root, relative))).resolve()
    base = root.resolve()
    if resolved != base and base not in resolved.parents:
        return None
    return resolved


def rebase_root_absolute(body: str, base: str) -> str:
    """Prefix root-absolute html/css URLs with the preview base.

    Values that already carry the preview base (a correctly configured build)
    pass through byte-identical, as do relative ones.
    """

    def rebase(match: re.Match) -> str:
        value = match.group(3)
        if value.startswith(base):
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{base}{value[1:]}"

    return _CSS_URL.sub(rebase, _ROOT_ABSOLUTE_URL.sub(rebase, body))


def inject_runtime(html: str) -> str:
    """Put PREVIEW_RUNTIME ahead of every script the page has.

    Right after <head> when there is one; otherwise after the doctype, never
    before it (that would drop the page into quirks mode).
    """
    head = _HEAD_OPEN.search(html)
    if head:
        return html[: head.end()] + PREVIEW_RUNTIME + html[head.end() :]
    doctype = _DOCTYPE.match(html)
    at = doctype.end() if doctype else 0
    return html[:at] + PREVIEW_RUNTIME + html[at:]


def cors_local_scripts(html: str) -> str:
    """Load the site's own classic scripts in CORS mode.

    The canvas frame's origin is opaque, so a plain <script src="script.js">
    counts as cross-origin and the browser reports every error in it as just
    "Script error." Marking the site's own (relative) scripts `crossorigin`
    makes them CORS requests, which the preview headers allow, so the real
    message and line reach the error relay. Scripts on other hosts are left
    alone: a CDN without CORS headers would refuse to load otherwise.
    """

    def mark(match: re.Match) -> str:
        tag = match.group(0)
        src = _SCRIPT_SRC.search(tag)
        if src is None or "crossorigin" in tag.lower() or _NOT_LOCAL.match(src.group(1)):
            return tag
        return "<script crossorigin" + tag[len("<script") :]

    return _SCRIPT_TAG.sub(mark, html)


def serve_root(workspace: Path) -> Path:
    """dist/ once a build produced an index there, else the workspace root."""
    dist = workspace / "dist"
    return dist if (dist / "index.html").is_file() else workspace


def resolve_preview(workspace: Path, base: str, path: str) -> tuple[bytes, str] | None:
    """The (body, mime) the preview serves at `path`, or None for a 404.

    A directory or extension-less miss falls back to the serve root's
    index.html; a site with no index at all gets the placeholder. html and css
    bodies are rebased onto `base`, and html pages get the preview runtime and
    CORS-mode local scripts.
    """
    requested = (path or "").replace("\\", "/")
    if blocked_segment(requested):
        return None
    root = serve_root(workspace)
    target = resolve_under(root, requested)
    if target is None:
        return None
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file() and not os.path.splitext(requested)[1]:
        target = root / "index.html"
    if not target.is_file():
        if not (root / "index.html").is_file():
            return inject_runtime(PREVIEW_PLACEHOLDER).encode("utf-8"), "text/html"
        return None
    body = target.read_bytes()
    mime = webpage_mime(target.name)
    if mime in ("text/html", "text/css"):
        text = rebase_root_absolute(body.decode("utf-8", errors="replace"), base)
        if mime == "text/html":
            text = inject_runtime(cors_local_scripts(text))
        body = text.encode("utf-8")
    return body, mime
