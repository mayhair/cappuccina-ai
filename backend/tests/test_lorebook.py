from pathlib import Path

import pytest

from agent import lorebook

ENTRY = """\
# Test Character
Aliases: Tester, Testy
Blurb: A made-up character for tests.

This is the body of the test character. It has a couple of sentences.
"""


def _write(directory: Path, filename: str, text: str) -> None:
    (directory / filename).write_text(text, encoding="utf-8")


def _corpus(tmp_path: Path) -> list[lorebook.Character]:
    _write(tmp_path, "test-character.md", ENTRY)
    _write(
        tmp_path,
        "second.md",
        "# Second One\nAliases: Two\nBlurb: Another test.\n\nBody of the second.\n",
    )
    return lorebook.load_characters(tmp_path)


# ---------- parsing ----------


def test_parses_entry_fields(tmp_path):
    characters = _corpus(tmp_path)
    character = next(c for c in characters if c.id == "test-character")
    assert character.name == "Test Character"
    assert character.aliases == ("Tester", "Testy")
    assert character.blurb == "A made-up character for tests."
    assert "body of the test character" in character.body


def test_missing_title_raises(tmp_path):
    _write(tmp_path, "bad.md", "No title here\nBlurb: x\n\nbody\n")
    with pytest.raises(lorebook.LoreBookError, match="must be '# Name'"):
        lorebook.load_characters(tmp_path)


def test_missing_blurb_raises(tmp_path):
    _write(tmp_path, "bad.md", "# Someone\n\nJust a body, no blurb.\n")
    with pytest.raises(lorebook.LoreBookError, match="Blurb"):
        lorebook.load_characters(tmp_path)


def test_missing_body_raises(tmp_path):
    _write(tmp_path, "bad.md", "# Someone\nBlurb: A line.\n")
    with pytest.raises(lorebook.LoreBookError, match="body"):
        lorebook.load_characters(tmp_path)


def test_duplicate_name_raises(tmp_path):
    _write(tmp_path, "a.md", "# Same\nBlurb: A.\n\nbody a\n")
    _write(tmp_path, "b.md", "# Same\nBlurb: B.\n\nbody b\n")
    with pytest.raises(lorebook.LoreBookError, match="unique"):
        lorebook.load_characters(tmp_path)


def test_duplicate_alias_raises(tmp_path):
    _write(tmp_path, "a.md", "# Alpha\nAliases: Shared\nBlurb: A.\n\nbody a\n")
    _write(tmp_path, "b.md", "# Beta\nAliases: Shared\nBlurb: B.\n\nbody b\n")
    with pytest.raises(lorebook.LoreBookError, match="unique"):
        lorebook.load_characters(tmp_path)


def test_missing_directory_is_empty():
    assert lorebook.load_characters(Path("does-not-exist-anywhere")) == []


# ---------- catalog ----------


def test_catalog_lines(tmp_path):
    lines = lorebook.catalog_lines(_corpus(tmp_path))
    assert (
        "- **Test Character** (Tester, Testy) — A made-up character for tests."
        in lines
    )
    assert "- **Second One** (Two) — Another test." in lines


def test_prompt_section_lists_the_real_cast():
    section = lorebook.prompt_section()
    assert "## Your lore book" in section
    assert "**Espressona Signora**" in section


def test_prompt_section_keeps_outsiders_out_of_the_book():
    """A question about another show's character is ordinary knowledge, not a miss."""
    section = lorebook.prompt_section()
    assert "Do not mention the lore book" in section
    assert "cartoon characters from other shows" in section


# ---------- lookup ----------


def test_lookup_exact_name(tmp_path):
    out = lorebook.lookup("Test Character", _corpus(tmp_path))
    assert out.startswith("# Test Character")
    assert "body of the test character" in out


def test_lookup_alias(tmp_path):
    out = lorebook.lookup("Testy", _corpus(tmp_path))
    assert out.startswith("# Test Character")


def test_lookup_is_case_and_accent_insensitive(tmp_path):
    characters = _corpus(tmp_path)
    assert lorebook.lookup("TEST CHARACTER", characters).startswith("# Test Character")
    assert lorebook.lookup("Tèst Chàracter", characters).startswith("# Test Character")


def test_lookup_survives_a_typo(tmp_path):
    out = lorebook.lookup("test charater", _corpus(tmp_path))
    assert out.startswith("# Test Character")


def test_lookup_fragment_inside_a_sentence(tmp_path):
    out = lorebook.lookup("tell me about Testy please", _corpus(tmp_path))
    assert out.startswith("# Test Character")


def test_lookup_miss_suggests_closest(tmp_path):
    _write(tmp_path, "alex.md", "# Alexander\nBlurb: A king.\n\nA body.\n")
    out = lorebook.lookup("Alexi", lorebook.load_characters(tmp_path))
    assert "No lore-book entry" in out
    assert "Alexander" in out


def test_lookup_empty_query_is_a_miss(tmp_path):
    out = lorebook.lookup("   ", _corpus(tmp_path))
    assert "No lore-book entry" in out


# ---------- real corpus ----------


def test_real_corpus_loads():
    ids = {c.id for c in lorebook.CHARACTERS}
    assert "espressona-signora" in ids
    assert len(lorebook.CHARACTERS) >= 8


def test_real_corpus_lookup_by_alias():
    out = lorebook.lookup("the espresso queen")
    assert "Espressona Signora" in out


# ---------- prompt + tool wiring ----------


def test_system_prompt_includes_lore_in_chat_mode():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("chat", None)
    assert "## Your lore book" in prompt
    assert "Espressona Signora" in prompt


def test_system_prompt_omits_lore_in_tutor_mode():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("tutor", "maths")
    assert "Your lore book" not in prompt


def test_lore_lookup_tool_returns_entry():
    from agent import tools

    out = tools.lore_lookup.invoke({"name": "Espressona Signora"})
    assert "Espressona Signora" in out
    assert "dark roast" in out


def test_lore_lookup_tool_schema():
    from agent import tools

    assert set(tools.lore_lookup.args) == {"name"}


def test_lore_lookup_is_registered():
    from agent import tools

    assert tools.lore_lookup in tools.ALL_TOOLS
    assert tools.TOOL_DISPLAY_NAMES["lore_lookup"] == "Lore Book"
