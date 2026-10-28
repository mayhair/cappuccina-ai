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


# ---------- reference images ----------


def _with_ref(tmp_path: Path, character_id: str, extension: str) -> Path:
    refs = tmp_path / "refs"
    refs.mkdir(exist_ok=True)
    ref = refs / f"{character_id}{extension}"
    ref.write_bytes(b"ref-bytes")
    return ref


def test_reference_image_is_resolved(tmp_path):
    _corpus(tmp_path)
    ref = _with_ref(tmp_path, "test-character", ".png")
    characters = lorebook.load_characters(tmp_path)
    character = next(c for c in characters if c.id == "test-character")
    assert character.ref_path == ref
    assert next(c for c in characters if c.id == "second").ref_path is None


def test_reference_extension_precedence(tmp_path):
    _corpus(tmp_path)
    _with_ref(tmp_path, "test-character", ".png")
    _with_ref(tmp_path, "test-character", ".jpg")
    character = next(
        c for c in lorebook.load_characters(tmp_path) if c.id == "test-character"
    )
    assert character.ref_path.suffix == ".png"


def test_catalog_marks_characters_with_a_reference(tmp_path):
    _corpus(tmp_path)
    _with_ref(tmp_path, "test-character", ".png")
    lines = lorebook.catalog_lines(lorebook.load_characters(tmp_path))
    assert "[official look available]" in next(
        line for line in lines if "Test Character" in line
    )
    assert "[official look available]" not in next(
        line for line in lines if "Second One" in line
    )


# ---------- the assistant's own look ----------


def test_assistant_reference_uses_refs_folder(tmp_path):
    _corpus(tmp_path)
    ref = _with_ref(tmp_path, lorebook.ASSISTANT_ID, ".png")
    assistant = lorebook.assistant_reference(tmp_path)
    assert assistant is not None
    assert assistant.name == "Ballerina Cappuccina"
    assert assistant.ref_path == ref


def test_assistant_reference_is_none_without_a_file(tmp_path):
    _corpus(tmp_path)
    assert lorebook.assistant_reference(tmp_path) is None


def test_reference_characters_include_the_assistant(tmp_path):
    _corpus(tmp_path)
    _with_ref(tmp_path, lorebook.ASSISTANT_ID, ".png")
    ids = {c.id for c in lorebook.reference_characters(tmp_path)}
    assert lorebook.ASSISTANT_ID in ids
    assert "test-character" in ids


def test_reference_characters_skip_the_assistant_without_a_look(tmp_path):
    characters = _corpus(tmp_path)
    ids = {c.id for c in lorebook.reference_characters(tmp_path)}
    assert lorebook.ASSISTANT_ID not in ids
    assert {c.id for c in characters} == {"test-character", "second"}


# ---------- find ----------


def test_find_returns_character_objects(tmp_path):
    characters = _corpus(tmp_path)
    assert lorebook.find("Testy", characters)[0].id == "test-character"
    assert lorebook.find("TEST CHARACTER", characters)[0].name == "Test Character"
    assert lorebook.find("nobody here", characters) == []


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


def test_system_prompt_default_keeps_young_user_safety():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("chat", None)
    assert "Users are young people aged 13 or older." in prompt
    assert "18 or older" not in prompt


def test_system_prompt_adult_swaps_young_user_safety():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("chat", None, adult=True)
    assert "## Safety for young users" not in prompt
    assert "Users are young people aged 13 or older." not in prompt
    assert "18 or older" in prompt
    # age-neutral rules survive the swap
    assert "never sends email" in prompt


def test_system_prompt_keeps_google_rules_when_linked():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("chat", None)
    assert "## Email (Gmail)" in prompt
    assert "## Calendar (Google Calendar)" in prompt
    assert "## Google services" not in prompt


def test_system_prompt_swaps_unlinked_google_rules_for_a_hint():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("chat", None, gmail=False)
    assert "## Email (Gmail)" not in prompt
    assert "## Calendar (Google Calendar)" in prompt
    assert "not linked their Gmail," in prompt

    prompt = build_system_prompt("chat", None, gmail=False, calendar=False)
    assert "## Calendar (Google Calendar)" not in prompt
    assert "not linked their Gmail or Google Calendar," in prompt


def test_graph_hides_tools_for_unlinked_google_services():
    from agent.graph import _tools_for
    from agent.tools import ALL_TOOLS, CALENDAR_TOOL_NAMES, GMAIL_TOOL_NAMES

    names = {t.name for t in _tools_for(ALL_TOOLS, gmail=False, calendar=True)}
    assert not names & GMAIL_TOOL_NAMES
    assert CALENDAR_TOOL_NAMES <= names
    names = {t.name for t in _tools_for(ALL_TOOLS, gmail=True, calendar=False)}
    assert GMAIL_TOOL_NAMES <= names
    assert not names & CALENDAR_TOOL_NAMES
    everything = _tools_for(ALL_TOOLS, True, True)
    assert len(everything) == len(ALL_TOOLS) - 1  # all but suggest_actions


def test_story_actions_are_never_offered_to_the_model():
    # The graph asks for next moves itself, so the model never sees the tool.
    from agent.graph import _tools_for
    from agent.tools import ALL_TOOLS, STORY_TOOL_NAMES

    names = {t.name for t in _tools_for(ALL_TOOLS, True, True)}
    assert not names & STORY_TOOL_NAMES
    names = {t.name for t in _tools_for(ALL_TOOLS, True, True, story={"world": "cappuccina"})}
    assert not names & STORY_TOOL_NAMES


def test_suggest_actions_cleans_and_caps_options():
    import json

    from agent.tools import suggest_actions

    out = json.loads(
        suggest_actions.invoke(
            {"options": ["  Run  ", "Run", "Hide", "Shout", "Sing", ""]}
        )
    )
    assert out == {"status": "story_choices", "options": ["Run", "Hide", "Shout"]}
    assert json.loads(suggest_actions.invoke({"options": []}))["status"] == "error"


def test_chat_prompt_keeps_chat_rhythm():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("chat", None)
    assert "Chat-bubble length" in prompt
    assert "at most one reference in a reply" in prompt
    assert "## Story mode" not in prompt
    assert "## Story boundaries" not in prompt


def test_story_prompt_narrates_with_story_boundaries_first():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("story", None)
    assert "Chat-bubble length" not in prompt
    assert "## Sample exchanges" not in prompt
    assert "## Story mode" in prompt
    assert "## Your lore book" in prompt
    # The story boundaries sit with the boundaries, ahead of persona and voice.
    assert prompt.index("## Story boundaries") < prompt.index("## Who she is")
    assert "## Story safety for young users" in prompt
    assert "## Story safety (adult mode)" not in prompt
    assert "Users are young people aged 13 or older." in prompt


def test_adult_story_prompt_swaps_story_safety():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("story", None, adult=True)
    assert "## Story safety (adult mode)" in prompt
    assert "## Story safety for young users" not in prompt
    assert "18 or older" in prompt
    # The shared story boundaries survive the swap.
    assert "Dark themes are allowed fiction" in prompt


def test_story_prompt_includes_scene_and_cast_bodies_only_when_set():
    from agent.prompts import build_system_prompt

    plain = build_system_prompt("story", None)
    assert "## The cast" not in plain
    assert "## This story's opening" not in plain

    prompt = build_system_prompt(
        "story",
        None,
        story={"scenario": "Café after closing", "cast": ["espressona-signora", "nobody"]},
    )
    assert "Café after closing" in prompt
    assert "## The cast" in prompt
    assert "### Espressona Signora" in prompt


def test_build_messages_adds_story_note_after_history():
    from agent import prompts

    history = [{"role": "user", "content": "hi"}]
    messages = prompts.build_messages(
        "story", None, None, history, story_note="## Story so far\n\nthey met"
    )
    assert messages[1] == history[0]
    assert messages[2]["content"].startswith("## Story so far")
    chat = prompts.build_messages(
        "chat", None, None, history, story_note="## Story so far\n\nthey met"
    )
    assert not any("Story so far" in m["content"] for m in chat)


def test_build_messages_threads_adult_flag():
    from agent import prompts

    messages = prompts.build_messages(
        "chat", None, None, [{"role": "user", "content": "hi"}], adult=True
    )
    assert "18 or older" in messages[0]["content"]


def test_today_uses_the_given_timezone():
    from datetime import datetime, timezone

    from agent.prompts import today

    now = datetime(2026, 9, 15, 23, 30, tzinfo=timezone.utc)
    assert today("Europe/Berlin", now) == "16 September 2026"
    assert today("UTC", now) == "15 September 2026"


def test_today_falls_back_to_utc_for_unknown_zone():
    from datetime import datetime, timezone

    from agent.prompts import today

    now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    assert today("Not/AZone", now) == "15 September 2026"
    assert today(None, now) == "15 September 2026"


def test_system_prompt_has_no_date():
    from agent.prompts import build_system_prompt

    assert "Current Date" not in build_system_prompt("chat", None)


def test_build_messages_puts_live_date_below_history(monkeypatch):
    from datetime import datetime, timezone

    from agent import prompts

    fixed = datetime(2026, 9, 15, 23, 30, tzinfo=timezone.utc)
    original = prompts.today
    monkeypatch.setattr(prompts, "today", lambda tz=None: original(tz, fixed))
    history = [{"role": "user", "content": "hi"}]

    messages = prompts.build_messages("chat", None, "Europe/Berlin", history)

    assert messages[0]["role"] == "system"
    assert "Current Date" not in messages[0]["content"]
    assert messages[1] is history[0]
    assert messages[-1] == {
        "role": "system",
        "content": "Current Date: 16 September 2026",
    }


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
