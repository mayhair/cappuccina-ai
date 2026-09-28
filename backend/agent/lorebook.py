"""Cappuccina's lore book — the cast of her world, one markdown file each.

Each file in ``lore/characters/`` describes one character::

    # Espressona Signora
    Aliases: Espresso Signora, the espresso queen
    Blurb: One line for the catalog the model sees every turn.

    The body: a short, in-universe description Cappuccina can retell.

Rules:

- The first non-blank line must be ``# Name``.
- ``Aliases:`` (comma- or semicolon-separated) and ``Blurb:`` are header lines
  directly under the title. The blurb is what the system-prompt catalog shows,
  so keep it to one short, in-character line.
- Everything else is the body, returned verbatim by the ``lore_lookup`` tool.
- Names and aliases must be unique across the book — loading fails loudly
  otherwise, because they are the keys the lookup matches on.

An optional official look lives in ``lore/characters/refs/``: one image named
after the markdown file stem (``refs/espressona-signora.png``). The image tools
attach it when the model names the character, so drawings stay consistent.
Extensions are tried in ``_REF_EXTENSIONS`` order.

The book is loaded once at import; edit the files and restart to change it.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

LORE_DIR = Path(__file__).resolve().parent / "lore"
CHARACTERS_DIR = LORE_DIR / "characters"
REFS_DIRNAME = "refs"
_REF_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

_HEADER_RE = re.compile(r"^(aliases|blurb)\s*:\s*(.*)$", re.IGNORECASE)
_ALIAS_SPLIT_RE = re.compile(r"[,;]")

# A query this short is only ever matched as a whole name/alias, never as a
# fragment of one, so "espresso" can't drag in every espresso-ish character.
_MIN_FRAGMENT = 3
_FUZZY_CUTOFF = 0.6
_SUGGESTION_CUTOFF = 0.4


class LoreBookError(RuntimeError):
    """A character file is malformed, or two characters share a name or alias."""


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    aliases: tuple[str, ...]
    blurb: str
    body: str
    ref_path: Path | None = None

    @property
    def keys(self) -> tuple[str, ...]:
        """Every name this character can be looked up by."""
        return (self.name, *self.aliases)


def _normalize(text: str) -> str:
    """Case-, accent- and punctuation-insensitive form used for matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    spaced = re.sub(r"[^\w\s]", " ", unaccented.casefold())
    return re.sub(r"\s+", " ", spaced).strip()


def _parse(path: Path) -> Character:
    lines = path.read_text(encoding="utf-8").splitlines()

    name = ""
    start = 0
    for index, line in enumerate(lines):
        if line.strip():
            if not line.lstrip().startswith("# "):
                raise LoreBookError(
                    f"{path.name}: first non-blank line must be '# Name'."
                )
            name = line.lstrip()[2:].strip()
            start = index + 1
            break
    if not name:
        raise LoreBookError(f"{path.name}: missing '# Name' title.")

    aliases: list[str] = []
    blurb = ""
    body_lines: list[str] = []
    for line in lines[start:]:
        if not body_lines:
            stripped = line.strip()
            if not stripped:
                continue
            match = _HEADER_RE.match(stripped)
            if match:
                key, value = match.group(1).lower(), match.group(2).strip()
                if key == "aliases":
                    aliases = [a.strip() for a in _ALIAS_SPLIT_RE.split(value) if a.strip()]
                else:
                    blurb = value
                continue
        body_lines.append(line)

    body = "\n".join(body_lines).strip()
    if not blurb:
        raise LoreBookError(f"{path.name}: missing 'Blurb:' line.")
    if not body:
        raise LoreBookError(f"{path.name}: missing body text.")
    return Character(
        id=path.stem, name=name, aliases=tuple(aliases), blurb=blurb, body=body
    )


def _check_unique(characters: list[Character]) -> None:
    seen: dict[str, str] = {}
    for character in characters:
        for key in character.keys:
            normalized = _normalize(key)
            if not normalized:
                continue
            owner = seen.get(normalized)
            if owner is not None and owner != character.id:
                raise LoreBookError(
                    f"'{key}' is used by both {owner} and {character.id}; "
                    "names and aliases must be unique."
                )
            seen[normalized] = character.id


def _ref_path(folder: Path, character_id: str) -> Path | None:
    """The character's reference image, trying each supported extension."""
    for extension in _REF_EXTENSIONS:
        candidate = folder / REFS_DIRNAME / f"{character_id}{extension}"
        if candidate.is_file():
            return candidate
    return None


def load_characters(directory: Path | None = None) -> list[Character]:
    """Parse every ``*.md`` in the corpus, loud on any mistake."""
    folder = CHARACTERS_DIR if directory is None else Path(directory)
    if not folder.is_dir():
        return []
    characters: list[Character] = []
    for path in sorted(folder.glob("*.md")):
        character = _parse(path)
        ref = _ref_path(folder, character.id)
        if ref is not None:
            character = replace(character, ref_path=ref)
        characters.append(character)
    _check_unique(characters)
    return characters


CHARACTERS = load_characters()


def catalog_lines(characters: list[Character] | None = None) -> list[str]:
    """The one-line-per-character index shown in the system prompt."""
    chars = CHARACTERS if characters is None else characters
    lines = []
    for character in chars:
        aliases = ", ".join(character.aliases)
        suffix = f" ({aliases})" if aliases else ""
        look = " [official look available]" if character.ref_path else ""
        lines.append(f"- **{character.name}**{suffix} — {character.blurb}{look}")
    return lines


_PROMPT_INTRO = (
    "## Your lore book\n\n"
    "These are the characters of your world — the universe you came from. You know "
    "them as neighbours, friends and rivals. The book is only about this world: when "
    "the user asks about one of these characters, call lore_lookup with their name to "
    "get the full story first, then tell it in your own voice, like a story about old "
    "neighbours — never read the entry out like a file.\n\n"
    "When you draw one of them, pass their name in the `characters` argument of "
    "generate_image or image_edit so they keep their official look.\n\n"
    "Questions about anyone else — cartoon characters from other shows, real people, "
    "historical figures, or anything from the wider world — are ordinary questions. "
    "Answer them from what you know, or use web_search when you need current facts. "
    "Do not mention the lore book, and do not tell the user the character 'isn't in "
    "your world', unless they specifically ask whether it is. Only when someone "
    "clearly asks about a character from your own universe and the book has no entry "
    "do you say, lightly, that you don't know that one yet."
)


def prompt_section(characters: list[Character] | None = None) -> str:
    """The system-prompt block: usage guidance plus the catalog."""
    chars = CHARACTERS if characters is None else characters
    if not chars:
        return ""
    return _PROMPT_INTRO + "\n\n" + "\n".join(catalog_lines(chars))


def _key_index(characters: list[Character]) -> dict[str, Character]:
    index: dict[str, Character] = {}
    for character in characters:
        for key in character.keys:
            normalized = _normalize(key)
            if normalized:
                index.setdefault(normalized, character)
    return index


def _entry_text(character: Character) -> str:
    return f"# {character.name}\n{character.body}"


def _miss(name: str, index: dict[str, Character]) -> str:
    label = name.strip() or "that name"
    message = f'No lore-book entry for "{label}".'
    close = difflib.get_close_matches(
        _normalize(name), list(index), n=3, cutoff=_SUGGESTION_CUTOFF
    )
    names: list[str] = []
    for key in close:
        candidate = index[key].name
        if candidate not in names:
            names.append(candidate)
    if names:
        message += " Closest names in the book: " + ", ".join(names) + "."
    return message


def _contained(query: str, characters: list[Character]) -> list[Character]:
    """Characters whose name or alias appears inside the query (or vice versa)."""
    hits: list[Character] = []
    for character in characters:
        for key in character.keys:
            normalized = _normalize(key)
            if not normalized:
                continue
            if normalized in query or (
                len(query) >= _MIN_FRAGMENT and query in normalized
            ):
                hits.append(character)
                break
    return hits


def find(name: str, characters: list[Character] | None = None) -> list[Character]:
    """Characters matching a name: exact, then contained, then one fuzzy near-miss."""
    chars = CHARACTERS if characters is None else characters
    index = _key_index(chars)
    query = _normalize(name)
    if not query or not index:
        return []

    if query in index:
        return [index[query]]

    contained = _contained(query, chars)
    if contained:
        return contained

    close = difflib.get_close_matches(query, list(index), n=1, cutoff=_FUZZY_CUTOFF)
    if close:
        return [index[close[0]]]

    return []


def lookup(name: str, characters: list[Character] | None = None) -> str:
    """Find the character(s) matching a name and return their entries.

    Matching is forgiving: exact name/alias, then containment, then a fuzzy
    near-miss. A miss returns a short note plus the closest names, so the model
    can correct itself or fall back to web_search.
    """
    chars = CHARACTERS if characters is None else characters
    matches = find(name, chars)
    if not matches:
        return _miss(name, _key_index(chars))
    return "\n\n---\n\n".join(_entry_text(character) for character in matches)
