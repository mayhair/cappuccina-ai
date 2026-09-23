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


def build_system_prompt(mode: str, subject: str | None, timezone: str | None = None) -> str:
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
    closing = f"Current Date: {today(timezone)}"
    if tutor:
        closing = f"{tutor}\n\n{closing}"
    sections = [BOUNDARIES, persona, voice]
    if lore:
        sections.append(lore)
    sections += [ATTACHMENT_NOTE, closing]
    return _JOINS.join(sections)
