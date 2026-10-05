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
When the user asks for a webpage, a website, a game, a quiz, or anything else \
that runs in a browser, build it one file at a time with write_webpage_file. \
Start with index.html, then add styles.css and script.js when they help — the \
page appears live in the user's canvas as you write. To change a page you made \
earlier, call read_webpage_file first, then write the whole file back. Keep it \
a single static page: no build step, and load pictures and fonts from https \
URLs or data URIs rather than local files."""


def build_system_prompt(mode: str, subject: str | None) -> str:
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
    sections = [BOUNDARIES, persona, voice]
    if lore:
        sections.append(lore)
    sections.append(ATTACHMENT_NOTE)
    sections.append(WEBPAGE_NOTE)
    if tutor:
        sections.append(tutor)
    return _JOINS.join(sections)


def date_line(timezone: str | None = None) -> str:
    return f"Current Date: {today(timezone)}"


def build_messages(mode: str, subject: str | None, timezone: str | None, history):
    """One model call's full message list.

    The system prompt leads (stable, so it stays cache-friendly), the chat
    history follows, and a trailing system note carries the current date. The
    date sits *after* the history so a long-running chat reads the live value
    instead of a date frozen at the top of the conversation.
    """
    return [
        {"role": "system", "content": build_system_prompt(mode, subject)},
        *history,
        {"role": "system", "content": date_line(timezone)},
    ]
