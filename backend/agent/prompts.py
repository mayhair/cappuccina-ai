from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent import lorebook
from app.config import TUTOR_FOR_SUBJECT, TUTOR_SUFFIX

LORE_DIR = Path(__file__).resolve().parent / "lore"


def today(tz: str | None = None, now: datetime | None = None) -> str:
    """The current date in ``tz``, formatted like "15 September 2026".

    Falls back to UTC when the zone name is unknown or the tz database is
    missing (Windows ships no zoneinfo data unless ``tzdata`` is installed).
    ``now`` is injectable for tests.
    """
    try:
        zone = ZoneInfo(tz) if tz else ZoneInfo("UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    current = (now or datetime.now(zone)).astimezone(zone)
    return f"{current.day} {current.strftime('%B %Y')}"


def _read(name: str) -> str:
    return (LORE_DIR / name).read_text(encoding="utf-8").strip()


def _split_sections(text: str) -> list[list[str]]:
    """Split markdown into blocks: everything before the first '## ' heading,
    then one block per '## ' heading."""
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.startswith("## "):
            blocks.append([line])
        else:
            blocks[-1].append(line)
    return blocks


def _without_sections(text: str, headings: set[str]) -> str:
    blocks = [
        "\n".join(block).strip()
        for block in _split_sections(text)
        if not (block and block[0].startswith("## ") and block[0][3:].strip() in headings)
    ]
    return "\n\n".join(block for block in blocks if block)


# Loaded once at import; edit the files in lore/ to change the character.
BOUNDARIES = _read("boundaries.md")
PERSONA = _read("persona.md")
VOICE = _read("voice.md")
ADULT_SAFETY = _read("safety-adult.md")

# The young-user safety block lives in the boundaries file under this heading;
# adult mode swaps it for the adult safety section.
_YOUNG_USER_SAFETY = {"Safety for young users"}

# Each Google service's rules live in the boundaries file under one heading.
# Until the user links that service its tools are not offered, so its rules are
# swapped for a one-line hint: cheaper, and she can still say how to connect it.
_GMAIL_RULES = {"Email (Gmail)"}
_CALENDAR_RULES = {"Calendar (Google Calendar)"}
GOOGLE_NOT_LINKED = (
    "## Google services\n\n"
    "The user has not linked their {services}, so you have no tools for it. "
    "If they ask, tell them they can connect it in Settings."
)

# Tutor mode keeps the persona's core but trims the decorative sections,
# so teaching instructions lead and the prompt stays focused.
_TUTOR_PERSONA_TRIM = {"Likes and dislikes", "Things she would say"}
_TUTOR_VOICE_TRIM = {"Sample exchanges"}

_JOINS = "\n\n---\n\n"

ATTACHMENT_NOTE = """\
The user can attach files to a message. Images arrive as pictures you can see \
directly. An attached document is listed by filename and id — before answering \
about it, call read_attachment with that id to read its text. Long documents \
come back in chunks; keep calling with the offset shown until you have what \
you need."""

WEBPAGE_NOTE = """\
When the user asks for a webpage, a website, a game, a quiz, or anything else
that runs in a browser, build it in their live website workspace. By default
write plain static code directly: start with index.html, then styles.css and
script.js when they help, in any folders you like (src/, pages/, img/ …) — the
page appears in the user's canvas as you write. Link files with RELATIVE paths
(`<link rel="stylesheet" href="styles.css">`), never root-absolute ones. Fonts
and libraries come from https URLs. To change something you made earlier, call
read_webpage_file first, then write the whole file back. A picture the user
attached, or one you drew with generate_image, goes into the site folder with
add_webpage_asset; the same goes for an audio, video or font file they attached
(you can't read those, but the page can play or load them). Reference it by the
name you gave it there (for example <img src="cat.png"> or
<audio controls src="song.mp3">).

If the user asks for a real app, framework or build step, use webpage_shell to
work in the container (bash, node, npm, git and curl are available; make
folders freely):
- React/Vite: `npm create vite@latest scaffold -- --template react`, then
  `cp -a scaffold/. . && rm -rf scaffold && npm install`
- Astro: `npm create astro@latest scaffold -- --template minimal --no-git --no-install --yes`
  then `cp -a scaffold/. . && rm -rf scaffold && npm install`
  (scaffold into a subfolder and copy it up: the site folder is never empty, so
  `npm create … .` would stop and ask a question; if a scaffold command needs
  different flags, run it and adapt)
- In the build config set `base: process.env.PREVIEW_BASE` (vite.config.js /
  astro.config.mjs). PREVIEW_BASE is already set in the container and is the
  only way the built site's asset links work in the user's canvas.
- Build with `npm run build`; the canvas serves dist/ automatically when it
  exists (otherwise the workspace root). Rebuild after every change.
- A tool outside node that a build needs (a compiler, imagemagick, jq …) can be
  installed with `sudo apt-get install -y <package>`; run `sudo apt-get update`
  first when apt asks for it. sudo runs apt-get/apt/dpkg only, and packages
  live in this site's container — keep the site itself on npm or plain files
  wherever you can.
- Client-side routing must be hash-based (`#/page`): the canvas preview is a
  sandboxed iframe where pushState URLs throw. Multi-page static output is fine.
Do not run git yourself; the system versions every change automatically."""


def build_system_prompt(
    mode: str,
    subject: str | None,
    adult: bool = False,
    gmail: bool = True,
    calendar: bool = True,
) -> str:
    if mode == "tutor":
        persona = _without_sections(PERSONA, _TUTOR_PERSONA_TRIM)
        voice = _without_sections(VOICE, _TUTOR_VOICE_TRIM)
        for_subject = TUTOR_FOR_SUBJECT.format(subject=subject) if subject else ""
        tutor = TUTOR_SUFFIX.format(for_subject=for_subject)
        lore = ""
    else:
        persona = PERSONA
        voice = VOICE
        tutor = ""
        lore = lorebook.prompt_section()
    if adult:
        boundaries = _without_sections(BOUNDARIES, _YOUNG_USER_SAFETY)
        boundaries = f"{boundaries}\n\n{ADULT_SAFETY}"
    else:
        boundaries = BOUNDARIES
    unlinked: list[str] = []
    if not gmail:
        boundaries = _without_sections(boundaries, _GMAIL_RULES)
        unlinked.append("Gmail")
    if not calendar:
        boundaries = _without_sections(boundaries, _CALENDAR_RULES)
        unlinked.append("Google Calendar")
    if unlinked:
        hint = GOOGLE_NOT_LINKED.format(services=" or ".join(unlinked))
        boundaries = f"{boundaries}\n\n{hint}"
    sections = [boundaries, persona, voice]
    if lore:
        sections.append(lore)
    sections.append(ATTACHMENT_NOTE)
    sections.append(WEBPAGE_NOTE)
    if tutor:
        sections.append(tutor)
    return _JOINS.join(sections)


def date_line(timezone: str | None = None) -> str:
    return f"Current Date: {today(timezone)}"


def build_messages(
    mode: str,
    subject: str | None,
    timezone: str | None,
    history,
    adult: bool = False,
    gmail: bool = True,
    calendar: bool = True,
):
    """One model call's full message list.

    The system prompt leads (stable, so it stays cache-friendly), the chat
    history follows, and a trailing system note carries the current date. The
    date sits *after* the history so a long-running chat reads the live value
    instead of a date frozen at the top of the conversation.
    """
    return [
        {"role": "system", "content": build_system_prompt(mode, subject, adult, gmail, calendar)},
        *history,
        {"role": "system", "content": date_line(timezone)},
    ]
