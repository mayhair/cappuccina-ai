"""FMHY free-media tools, proxied through the FMHY MCP server.

The remote server at ``FMHY_MCP_URL`` is discovered once at app startup with
``langchain.mcp.MCPAdapter`` and its tools are re-exposed here as a small,
hand-written set. Re-exposing them (rather than binding the server's tools
directly) buys three things this product needs:

* gating language in the docstrings, so the model treats free-media lookups as
  a last resort instead of a first answer;
* an NSFW filter on the way out, so entries the FMHY dataset tags ``NSFW`` never
  reach a young user;
* stable tool names (``fmhy_*``) that the graph keys on to inject the ad-blocker
  safety note.

The upstream tools stay callable after the adapter's discovery context exits:
each call opens its own short-lived connection, which is exactly what the
stateless MCP spec is for.
"""

import asyncio
import logging
import re
from typing import Literal

from langchain_core.tools import tool

from agent.content import flatten_content
from app.config import FMHY_MCP_URL

logger = logging.getLogger(__name__)

# Names the graph watches for when deciding whether to inject the safety note.
# These are the wrappers defined below, not necessarily the upstream names.
FMHY_TOOL_NAMES = frozenset(
    {
        "fmhy_media_sites",
        "fmhy_search",
        "fmhy_get_links",
        "fmhy_get_section",
        "fmhy_list_sections",
    }
)

# The subset of the above that the remote server is expected to expose. Used only
# for the startup discovery check; fmhy_media_sites is answered from get_section.
_UPSTREAM_TOOL_NAMES = frozenset(
    {
        "fmhy_search",
        "fmhy_get_links",
        "fmhy_get_section",
        "fmhy_list_sections",
    }
)

# Injected as a SystemMessage right after the FMHY tools run — never up front in
# the system prompt. The model turns this into one short line of user advice.
FMHY_SAFETY_NOTE = (
    "You just looked something up in the FMHY free-media guide. Every time you "
    "share free-media links, include a short, friendly safety reminder in your "
    "own voice: free-media sites are full of shady ads and pop-ups, so use a "
    "proper ad-blocker before visiting them. Recommend the [AdGuard browser "
    "extension](https://adguard.com/en/adguard-browser-extension/overview.html) "
    "or the [Brave web browser](https://brave.com). Keep it to a sentence or two "
    "and do not let it crowd out the actual answer."
)

# FMHY tags entries in their descriptions: "/ NSFW" or "Some NSFW". The dataset
# has no dedicated adult section, so this line-level filter is the whole guard.
_NSFW = re.compile(r"\bNSFW\b", re.IGNORECASE)
_NUMBERED = re.compile(r"^\s*\d+\.\s")

_GATING = (
    "Only call this after the user has clearly insisted on finding free sources "
    "(asked again, said they can't pay, or asked specifically where to watch or "
    "play something for free). Before that, offer the ordinary free options "
    "instead. The FMHY guide mixes free and pirated links; say so when you use "
    "it, prefer its starred (⭐/🌟) and official entries, and never surface "
    "adult/NSFW entries."
)

# FMHY is a directory of websites grouped into sections, so "where can I watch
# X" is answered by reading the right section, never by searching for the title.
# The bulk of the links live in the full sections ("Stream Aggregators" and
# friends), not the short overview blocks ("Movies / Shows") at the top of the
# guide. Every name below was verified against the live endpoint — some of them
# are fuzzy queries rather than literal headings (the server matches them), e.g.
# "Movie Downloading" resolves to the heading "Downloading / تحميل".
#
# core     - the big lists for this kind, tried first, in order
# niche    - the smaller specialty lists, appended after the core ones
# beginner - the short overview block, used only if every full list is empty
# resolver - keyword for the search fallback that finds a renamed section
#
# Keep the keys in step with the Literal in fmhy_media_sites.
_MEDIA_PLAN: dict[str, dict[str, object]] = {
    "movies_shows": {
        "core": ["Stream Aggregators", "P-Stream Forks", "TV Streaming"],
        "niche": [
            "Drama Streaming",
            "Cartoons",
            "Documentaries",
            "Classic Movies",
            "Live TV",
            "Movie Downloading",
            "Movies Torrenting",
        ],
        "beginner": "Movies / Shows",
        "resolver": "Stream Aggregators",
    },
    "anime": {
        "core": ["Anime Streaming", "Anime Downloading", "Anime Torrenting"],
        "niche": ["Anime Apps"],
        "beginner": "Anime",
        "resolver": "Anime Streaming",
    },
    "music": {
        "core": [
            "Streaming Sites",  # the audio one; the video list is reached via
            "Streaming Apps",  # "Stream Aggregators" above
            "Audio Ripping Sites",
            "Audio Torrenting",
        ],
        "niche": ["Genre Specific Streaming"],
        "beginner": "Music",
        "resolver": "Audio Streaming",
    },
    "games": {
        "core": ["Download Games", "Game Repacks", "ROM Sites"],
        "niche": ["Roms", "Console Games"],
        "beginner": "Gaming",
        "resolver": "Download Games",
    },
    "books": {
        "core": ["Ebooks", "Comics", "Manga", "Light Novels", "Textbooks"],
        "niche": ["Academic Papers", "Newspapers", "Download Audiobooks"],
        "beginner": "Reading",
        "resolver": "Ebooks",
    },
    "software": {
        "core": ["Software Sites"],
        "niche": [],
        "beginner": "Windows Guide",
        "resolver": "Software Sites",
    },
}

# Presentation: 🌟 and ⭐ entries first, long tail dropped, so one tool result
# stays readable while still offering many places to try.
_STAR_RANK = {"🌟": 0, "⭐": 1}
_CAP_PER_SECTION = 40
_TOTAL_BUDGET = 25000
_SECTION_MAX_CHARS = 20000

# The single rule the model was missing: a title is never in this dataset.
_NOT_A_CATALOG = (
    "These are websites, not a title catalog. Suggest one or two of them and tell "
    "the user to search for the title on those sites. If a site is down or does "
    "not have the title, suggest another from the list. Never say a title is "
    "unavailable because it is not listed here — FMHY never lists titles."
)

# Appended to free-form search results so an empty or off-topic match is never
# read as "this title doesn't exist".
_SEARCH_CAVEAT = (
    "\n\n(Reminder: FMHY lists websites and sections, not titles. These matches "
    "say nothing about whether a specific movie, show or game is available — use "
    "fmhy_media_sites for that.)"
)

# The discovered upstream tools, replaced on every successful load.
_originals: dict[str, object] = {}


def strip_nsfw(text: str) -> str:
    """Drop NSFW-tagged entries from an FMHY result.

    Two shapes arrive from the server:

    * section text — plain markdown bullets, where the tag sits on the same line
      as the entry, so the line can go on its own;
    * search/link results — numbered records with continuation lines, where the
      tag can sit on a ``Snippet:`` line while the URL lives on the next line, so
      the whole record has to go together.
    """
    if not text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    total = len(lines)
    while i < total:
        line = lines[i]
        if _NUMBERED.match(line):
            record = [line]
            j = i + 1
            while j < total and not _NUMBERED.match(lines[j]):
                record.append(lines[j])
                j += 1
            if not any(_NSFW.search(part) for part in record):
                out.extend(record)
            i = j
        else:
            if not _NSFW.search(line):
                out.append(line)
            i += 1
    return "\n".join(out)


async def _invoke_untraced(tool, args: dict):
    """Call the upstream MCP tool without spawning a nested tool run.

    The wrapper tools are themselves tools, so calling the original through
    ``ainvoke`` makes LangChain emit a second ``on_tool_start`` for the same
    name; the chat stream surfaces every one of those as its own tool card,
    which looks like the model called the tool twice. The plain coroutine
    keeps the call internal to this wrapper. It returns ``(blocks, artifact)``
    where ``ainvoke`` returns just the blocks, so unwrap it.
    """
    coroutine = getattr(tool, "coroutine", None)
    if not callable(coroutine):
        return await tool.ainvoke(args)
    result = await coroutine(**args)
    if isinstance(result, tuple) and result:
        return result[0]
    return result


async def _call(name: str, args: dict) -> str:
    original = _originals.get(name)
    if original is None:
        return "The free-media guide isn't available right now."
    try:
        result = await _invoke_untraced(original, args)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, not raised
        logger.warning("FMHY tool %s failed: %s", name, exc)
        return f"Couldn't reach the free-media guide ({exc})."
    return strip_nsfw(flatten_content(result))


# --- section assembly -------------------------------------------------------


def _is_entry_bullet(line: str) -> bool:
    """True for a site entry line (``* 🌟 **[Name](url)** - ...``)."""
    return line.startswith("* ") and "**Note**" not in line


def _bullet_rank(line: str) -> int:
    """🌟 first, then ⭐, then everything else (stable inside each rank)."""
    head = line[:12]
    for star, rank in _STAR_RANK.items():
        if star in head:
            return rank
    return 2


def _has_entries(text: str) -> bool:
    """True when a section body lists entries, not just a heading."""
    return any(_is_entry_bullet(line) for line in text.splitlines())


def _arrange(text: str, cap: int) -> tuple[str, int]:
    """Star-first ordering and a cap over one section body.

    Headings and notes stay put; entry bullets are reordered 🌟 → ⭐ → rest and
    truncated to ``cap`` across the whole section. Returns the rendered text and
    how many bullets were dropped.
    """
    groups: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.startswith("#") and groups[-1]:
            groups.append([])
        groups[-1].append(line)

    rendered: list[str] = []
    dropped = 0
    remaining = cap
    for group in groups:
        bullets = [line for line in group if _is_entry_bullet(line)]
        bullets.sort(key=_bullet_rank)
        kept, extra = bullets[:remaining], bullets[remaining:]
        remaining -= len(kept)
        dropped += len(extra)
        placed = False
        for line in group:
            if _is_entry_bullet(line):
                if not placed:
                    rendered.extend(kept)
                    placed = True
                continue
            rendered.append(line)
    return "\n".join(rendered), dropped


def _fit(text: str, budget: int) -> tuple[str, int]:
    """Drop tail entry bullets until the text fits ``budget`` characters.

    Combined with the star-first ordering this drops unstarred entries first.
    """
    lines = text.splitlines()
    dropped = 0
    while len("\n".join(lines)) > budget:
        last = -1
        for index, line in enumerate(lines):
            if _is_entry_bullet(line):
                last = index
        if last == -1:
            break
        del lines[last]
        dropped += 1
    return "\n".join(lines), dropped


_RECORD_SECTION = re.compile(r"^\s*\d+\.\s+\[section\]")
_NEXT_RECORD = re.compile(r"^\s*\d+\.\s")
_SECTION_LINE = re.compile(r"^\s*Section:\s*(.+?)\s*$")


def _section_from_search(text: str) -> str:
    """The heading of the first ``[section]`` hit in a search result, or ''."""
    watching = False
    for line in text.splitlines():
        if _RECORD_SECTION.match(line):
            watching = True
            continue
        if _NEXT_RECORD.match(line):
            watching = False
            continue
        if watching:
            match = _SECTION_LINE.match(line)
            if match:
                return match.group(1)
    return ""


async def _refresh_dataset(timeout: float = 10.0) -> None:
    """Force-refresh the server's cached dataset once at startup. Never raises.

    The section map above is keyed on FMHY's headings, so a fresh cache keeps
    those headings from drifting out from under us. Best effort only: a slow or
    broken refresh must not stop the tools from loading.
    """
    refresher = _originals.get("fmhy_refresh_cache")
    if refresher is None:
        return
    try:
        await asyncio.wait_for(_invoke_untraced(refresher, {}), timeout=timeout)
    except Exception:  # noqa: BLE001 - startup must not depend on this
        logger.info("FMHY cache refresh skipped", exc_info=True)


@tool
async def fmhy_media_sites(
    media_kind: Literal[
        "movies_shows", "anime", "music", "games", "books", "software"
    ],
    title: str = "",
) -> str:
    """List the free-media sites FMHY recommends for a kind of media.

    ALWAYS use this — never fmhy_search — when the user asks where to watch,
    listen to, play or read a specific title. The guide is a directory of
    websites grouped by category, so a title is never found by searching for it:
    pick the category here and suggest sites from it instead. Returns the guide's
    full site lists with the starred (🌟/⭐) picks first, so there are plenty of
    fallbacks when one site is down or lacks the title. Pass the title only so
    the answer can name it; it is never sent to the guide.
    """
    plan = _MEDIA_PLAN.get(media_kind)
    if plan is None:
        return "Unknown media kind. Pick one of: " + ", ".join(sorted(_MEDIA_PLAN)) + "."
    core = list(plan["core"])  # pyrefly: ignore[unsupported-attribute]
    niche = list(plan["niche"])
    beginner = str(plan["beginner"])
    resolver = str(plan["resolver"])

    names = [*core, *niche]
    raws = await asyncio.gather(
        *(
            _call("fmhy_get_section", {"title_or_slug": name, "max_chars": _SECTION_MAX_CHARS})
            for name in names
        )
    )
    bodies = [(name, raw) for name, raw in zip(names, raws) if _has_entries(raw)]

    # Thorough search first; the short overview is the last resort.
    if not bodies:
        found = _section_from_search(
            await _call("fmhy_search", {"query": resolver, "max_results": 3})
        )
        if found:
            raw = await _call(
                "fmhy_get_section",
                {"title_or_slug": found, "max_chars": _SECTION_MAX_CHARS},
            )
            if _has_entries(raw):
                bodies.append((found, raw))

    overview_note = ""
    if not bodies:
        raw = await _call(
            "fmhy_get_section", {"title_or_slug": beginner, "max_chars": _SECTION_MAX_CHARS}
        )
        if _has_entries(raw):
            bodies.append((beginner, raw))
            overview_note = (
                "> Short overview from the guide — the full lists weren't "
                "reachable just now.\n\n"
            )

    chunks: list[str] = []
    used = 0
    trimmed = False
    for _name, raw in bodies:
        rendered, dropped = _arrange(raw, _CAP_PER_SECTION)
        fitted, more = _fit(rendered, _TOTAL_BUDGET - used)
        dropped += more
        if not fitted.strip():
            trimmed = True
            break
        if dropped:
            fitted += f"\n> …and {dropped} more entries here (ask if you want more)."
        chunks.append(fitted)
        used += len(fitted) + 5
        if more:
            trimmed = True
            break

    if chunks:
        body_text = "\n\n***\n\n".join(chunks)
    else:
        body_text = "> The free-media guide didn't return any lists just now."
    if trimmed:
        body_text += (
            "\n\n> Some sections and entries were trimmed for length — ask if "
            "you want more."
        )

    heading = f"Free-media sites for {media_kind.replace('_', ' ')}"
    if title.strip():
        heading += f" — *{title.strip()}*"
    return f"{heading}\n\n{overview_note}{body_text}\n\n{_NOT_A_CATALOG}"


@tool
async def fmhy_search(query: str, max_results: int = 5, section: str = "") -> str:
    """Search the FMHY free-media guide for sections and website entries.

    The query matches *site and section names*, never titles: searching for a
    movie, show, anime, game or book title returns unrelated junk and says
    nothing about whether that title exists. Use this for directory lookups
    (e.g. "font sites", "wallpapers", "adblockers"); for a specific title use
    fmhy_media_sites. Returns sections and entries with links.
    """
    args = {"query": query, "max_results": max_results, "include_urls": True}
    if section:
        args["section"] = section
    return await _call("fmhy_search", args) + _SEARCH_CAVEAT


@tool
async def fmhy_get_links(query: str, max_results: int = 5, section: str = "") -> str:
    """Return FMHY website entries that have links for a site/section query.

    The query matches *site and section names*, never titles: never pass a movie,
    show, anime, game or book title — it returns unrelated junk. Use this once
    you know which site or section the user wants and need actual URLs to share;
    for a specific title use fmhy_media_sites.
    """
    args = {"query": query, "max_results": max_results}
    if section:
        args["section"] = section
    return await _call("fmhy_get_links", args) + _SEARCH_CAVEAT


@tool
async def fmhy_get_section(title_or_slug: str, max_chars: int = _SECTION_MAX_CHARS) -> str:
    """Read one full section of the FMHY free-media guide by its heading or slug.

    Use after fmhy_list_sections or a search result names the exact section you
    need.
    """
    return await _call(
        "fmhy_get_section",
        {"title_or_slug": title_or_slug, "max_chars": max_chars},
    )


@tool
async def fmhy_list_sections() -> str:
    """List the FMHY guide's section headings so you can pick where to look."""
    return await _call("fmhy_list_sections", {})


# The tools exposed to the model, in the order they are registered. The graph
# only binds these if the server answered at startup (see load_fmhy_tools).
def _all_wrappers() -> list:
    return [
        fmhy_media_sites,
        fmhy_search,
        fmhy_get_links,
        fmhy_get_section,
        fmhy_list_sections,
    ]


# The gating rule leads every description so it is read at tool-choice time.
for _wrapper in _all_wrappers():
    _wrapper.description = f"{_GATING} {_wrapper.description}"


def is_fmhy_tool_message(msg) -> bool:
    """True when a ToolMessage was produced by one of the FMHY tools."""
    return getattr(msg, "name", "") in FMHY_TOOL_NAMES


def has_fmhy_safety_note(messages) -> bool:
    """True when this run's messages already carry the injected safety note."""
    for msg in messages:
        if msg.__class__.__name__ != "SystemMessage":
            continue
        kwargs = getattr(msg, "additional_kwargs", None) or {}
        if kwargs.get("fmhy_safety_note"):
            return True
    return False


def safety_note_for(batch, existing_messages):
    """The SystemMessage to append after a tool batch, or None.

    Injected only once per run, and only when an FMHY tool actually ran — this
    is the "when the tools are called, not before" rule.
    """
    if not any(is_fmhy_tool_message(msg) for msg in batch):
        return None
    if has_fmhy_safety_note(existing_messages):
        return None
    from langchain_core.messages import SystemMessage

    return SystemMessage(
        content=FMHY_SAFETY_NOTE,
        additional_kwargs={"fmhy_safety_note": True},
    )


async def load_fmhy_tools(timeout: float = 15.0) -> list:
    """Discover the FMHY tools over MCP. Returns [] on any failure.

    Called once at app startup; a down or slow endpoint must never stop the
    chatbot from booting.
    """
    if not FMHY_MCP_URL:
        return []
    try:
        from langchain.mcp import MCPAdapter

        async def _discover():
            adapter = MCPAdapter(FMHY_MCP_URL)
            return await adapter.list_tools()

        tools = await asyncio.wait_for(_discover(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FMHY MCP server unavailable (%s); free-media tools disabled", exc)
        return []

    _originals.clear()
    for tool_obj in tools:
        _originals[tool_obj.name] = tool_obj
    missing = [name for name in _UPSTREAM_TOOL_NAMES if name not in _originals]
    if missing:
        logger.warning("FMHY MCP server did not expose %s", ", ".join(sorted(missing)))
    await _refresh_dataset()
    return _all_wrappers()
