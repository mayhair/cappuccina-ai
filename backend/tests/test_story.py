from agent import story


def _chapter(id, end_uid, text="t", start_uid=None):
    return {"id": id, "start_uid": start_uid, "end_uid": end_uid, "text": text}


def test_chapters_on_path_keeps_this_branch_in_story_order():
    path = ["a", "b", "c", "d"]
    chapters = [
        _chapter(3, "d"),
        _chapter(1, "b"),
        _chapter(2, "x"),  # on a branch the user left
        _chapter(4, None, "a note"),  # hand-written, always applies
    ]
    assert [c["id"] for c in story.chapters_on_path(chapters, path)] == [1, 3, 4]


def test_pending_section_waits_for_a_full_chapter():
    path = [str(i) for i in range(7)]
    assert story.pending_section([], path, 8) is None
    assert story.pending_section([], path, 7) == (0, 7)
    covered = [_chapter(1, "2")]
    assert story.pending_section(covered, path, 4) == (3, 7)
    assert story.pending_section(covered, path, 5) is None


def test_story_note_numbers_chapters_and_skips_blank_ones():
    note = story.story_note([_chapter(1, "a", "they met"), _chapter(2, "b", "  ")])
    assert note.startswith("## Story so far")
    assert "### Chapter 1\nthey met" in note
    assert "Chapter 2" not in note
    assert story.story_note([]) == ""


def test_trim_start_drops_only_covered_messages_over_budget():
    sizes = [400] * 10  # 100 tokens each, 1000 total
    assert story.trim_start(sizes, covered=10, budget_tokens=1000) == 0
    assert story.trim_start(sizes, covered=10, budget_tokens=750) == 3
    # Nothing covered: nothing is dropped, however long the history is.
    assert story.trim_start(sizes, covered=0, budget_tokens=10) == 0
    assert story.trim_start(sizes, covered=2, budget_tokens=10) == 2


# ---------- REST ----------

def test_story_settings_and_chat_creation(signed_in):
    c = signed_in
    assert c.get("/api/v1/auth/me").json()["storyChoices"] is True
    assert c.patch("/api/v1/auth/me", json={"storyChoices": False}).json()["storyChoices"] is False

    made = c.post(
        "/api/v1/chats",
        json={"mode": "story", "story": {"scenario": "Café after closing", "cast": ["tralalero-tralala"]}},
    )
    assert made.status_code == 200
    assert made.json()["story"]["cast"] == ["tralalero-tralala"]
    listed = c.get("/api/v1/chats").json()
    assert listed[0]["mode"] == "story"
    assert listed[0]["story"]["scenario"] == "Café after closing"
    assert c.post("/api/v1/chats", json={"mode": "dance"}).status_code == 400


def test_stream_rejects_unknown_modes(signed_in):
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    bad = signed_in.post(
        "/api/v1/chat/stream",
        json={"chatId": chat["id"], "messages": [{"role": "user", "content": "hi"}], "mode": "dance"},
    )
    assert bad.status_code == 400


def test_story_chapter_rest_roundtrip(signed_in, monkeypatch):
    from app import db

    c = signed_in
    chat = c.post("/api/v1/chats", json={"mode": "story"}).json()
    base = f"/api/v1/chats/{chat['id']}/story/chapters"
    assert c.get(base).json()["chapters"] == []

    auto = db.create_story_chapter(chat["id"], "They met backstage.", "u1", "u2")
    note = c.post(base, json={"text": "Tralalero owes us a race."}).json()
    assert note["edited"] is True and note["endUid"] is None

    edited = c.patch(f"{base}/{auto['id']}", json={"text": "They met backstage, nervous."}).json()
    assert edited["edited"] is True
    assert c.patch(f"{base}/{auto['id']}", json={"text": "  "}).status_code == 400
    assert c.post(f"{base}/{note['id']}/resummarize").status_code == 400

    assert c.delete(f"{base}/{note['id']}").json() == {"ok": True}
    assert c.delete(f"{base}/{note['id']}").status_code == 404
    assert [ch["id"] for ch in c.get(base).json()["chapters"]] == [auto["id"]]


def test_story_chapters_are_private_to_the_chat_owner(signed_in):
    from app import db

    other = db.create_google_user("other@example.com", "Other", "google-sub-2")
    chat = db.create_chat(other["id"], mode="story")
    assert signed_in.get(f"/api/v1/chats/{chat['id']}/story/chapters").status_code == 404


# ---------- worlds and roles ----------

def test_story_without_a_world_is_her_world_with_her_in_it():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt("story", None, story={"scenario": "Café", "cast": []})
    assert "## Your lore book" in prompt
    assert "## Cappuccina's world" in prompt
    assert "## Your role: in the scene" in prompt
    assert "## Your role: narrator" not in prompt


def test_other_worlds_leave_her_lore_out_and_make_her_the_narrator():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt(
        "story", None, story={"world": "scifi", "withCappuccina": False, "player": "A pilot"}
    )
    assert "## Your lore book" not in prompt
    assert "## Cappuccina's world" not in prompt
    assert "Tralalero" not in prompt
    assert "## Your role: narrator" in prompt
    assert "## Your role: in the scene" not in prompt
    assert "science-fiction world" in prompt
    assert "## Who the user plays\n" in prompt
    # As narrator the decorative persona and her flavor go; who she is stays
    # for OOC. Chat rules of speech never apply to a story.
    assert "## Likes and dislikes" not in prompt
    assert "## How she thinks about herself" not in prompt
    assert "## Signature flavor" not in prompt
    assert "# Voice" not in prompt
    assert "## Rules of speech" not in prompt
    assert "## Who she is" in prompt
    assert "## Personality traits" in prompt

    in_scene = build_system_prompt("story", None, story={"world": "cappuccina"})
    assert "## How she thinks about herself" in in_scene
    assert "## Signature flavor" in in_scene
    assert "## Rules of speech" not in in_scene
    assert "## Rules of speech" in build_system_prompt("chat", None)


def test_story_prompt_leaves_out_what_a_story_never_needs():
    from agent.prompts import MATH_NOTE, WEBPAGE_NOTE, build_system_prompt

    prompt = build_system_prompt("story", None, story={"world": "fantasy"}, gmail=False)
    for gone in (
        "## Free media and where to watch things",
        "## Email (Gmail)",
        "## Calendar (Google Calendar)",
        "## Google services",
        "## Teaching honesty",
        "## Interaction with lore",
        MATH_NOTE,
        WEBPAGE_NOTE,
    ):
        assert gone not in prompt, gone
    assert "## Honesty about being an AI" in prompt
    assert "## Story mode" in prompt

    her_world = build_system_prompt("story", None, story={"world": "cappuccina"})
    assert "## Interaction with lore" in her_world
    assert "## Your lore book" in her_world
    # Chat keeps all of it.
    chat = build_system_prompt("chat", None)
    assert WEBPAGE_NOTE in chat and "## Free media and where to watch things" in chat


def test_story_mode_offers_only_the_tools_a_story_uses():
    from types import SimpleNamespace

    from agent.graph import _tools_for
    from agent.tools import ALL_TOOLS

    tools = ALL_TOOLS + [SimpleNamespace(name="fmhy_search")]
    fantasy = {t.name for t in _tools_for(tools, True, True, story={"world": "fantasy"})}
    for gone in ("write_webpage_file", "webpage_shell", "check_webpage", "search_emails",
                 "create_event", "solve_equation", "fmhy_search", "lore_lookup"):
        assert gone not in fantasy, gone
    for kept in ("generate_image", "image_edit", "view_image", "read_attachment",
                 "web_search", "read_webpage", "save_memory"):
        assert kept in fantasy, kept
    her_world = {t.name for t in _tools_for(tools, True, True, story={})}
    assert "lore_lookup" in her_world
    chat = {t.name for t in _tools_for(tools, True, True)}
    assert {"write_webpage_file", "fmhy_search", "solve_equation"} <= chat


def test_lore_cast_brings_the_lore_book_into_another_world():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt(
        "story", None, story={"world": "fantasy", "cast": ["bombardiro-crocodilo"]}
    )
    assert "## Your lore book" in prompt
    assert "### Bombardiro Crocodilo" in prompt
    assert "## Cappuccina's world" not in prompt


def test_fandom_and_custom_worlds_use_the_users_text():
    from agent.prompts import build_system_prompt

    fandom = build_system_prompt("story", None, story={"world": "fandom", "worldText": "Pokémon, Kanto"})
    assert "existing fandom" in fandom and "Pokémon, Kanto" in fandom
    custom = build_system_prompt("story", None, story={"world": "custom", "worldText": "A city on a whale"})
    assert "A world the user made up" in custom and "A city on a whale" in custom


def test_saved_characters_render_and_a_bare_name_is_allowed():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt(
        "story",
        None,
        story={
            "world": "fandom",
            "worldText": "Pokémon",
            "characters": [
                {"name": "Aria", "description": "An elf ranger", "voice": "Clipped"},
                {"name": "Pikachu", "description": "", "voice": ""},
            ],
        },
    )
    assert "### Aria\nAn elf ranger\nHow they talk: Clipped" in prompt
    assert "### Pikachu\nNo description given" in prompt
    assert "generate_image" not in prompt.split("## The cast")[1].split("###")[0]


def test_story_state_loads_only_the_users_own_characters(signed_in):
    from app import db
    from app.main import _resolved_story
    from app.schemas import StorySetup

    me = db.get_user_by_google_sub("google-sub-1")
    mine = db.create_story_character(me["id"], "Aria", "An elf ranger", "Clipped")
    other = db.create_google_user("other@example.com", "Other", "google-sub-2")
    theirs = db.create_story_character(other["id"], "Secret", "Not yours", "")

    story = _resolved_story(
        StorySetup(world="fantasy", characters=[theirs["id"], mine["id"], 99999]), me
    )
    assert story["characters"] == [
        {"name": "Aria", "description": "An elf ranger", "voice": "Clipped"}
    ]
    assert story["world"] == "fantasy" and story["withCappuccina"] is True


def test_the_users_own_character_is_who_they_play():
    from agent.prompts import build_system_prompt

    prompt = build_system_prompt(
        "story",
        None,
        story={
            "world": "fantasy",
            "player": "Starts the story hurt",
            "playerCharacter": {"name": "Aria", "description": "An elf ranger", "voice": ""},
        },
    )
    plays = prompt.split("## Who the user plays")[1]
    assert "The user plays Aria" in plays
    assert "### Aria\nAn elf ranger" in plays
    assert "Their notes for this story: Starts the story hurt" in plays
    assert "## The cast" not in prompt


def test_the_played_character_is_loaded_by_owner_and_left_out_of_the_cast(signed_in):
    from app import db
    from app.main import _resolved_story
    from app.schemas import StorySetup

    me = db.get_user_by_google_sub("google-sub-1")
    aria = db.create_story_character(me["id"], "Aria", "An elf ranger", "")
    bo = db.create_story_character(me["id"], "Bo", "", "")
    other = db.create_google_user("other@example.com", "Other", "google-sub-2")
    theirs = db.create_story_character(other["id"], "Secret", "Not yours", "")

    story = _resolved_story(
        StorySetup(characters=[aria["id"], bo["id"]], playerCharacter=aria["id"]), me
    )
    assert story["playerCharacter"]["name"] == "Aria"
    assert [c["name"] for c in story["characters"]] == ["Bo"]
    stolen = _resolved_story(StorySetup(playerCharacter=theirs["id"]), me)
    assert stolen["playerCharacter"] is None


def test_story_chats_leave_memories_out(signed_in):
    from agent.graph import _tools_for
    from agent.prompts import build_messages
    from agent.tools import ALL_TOOLS, MEMORY_TOOL_NAMES
    from app import db
    from app.main import _state
    from app.schemas import ChatRequest

    me = db.get_user_by_google_sub("google-sub-1")
    db.create_memory(me["id"], "The user is a nurse in Lahore")
    chat = signed_in.post("/api/v1/chats", json={"mode": "story"}).json()

    def state_for(mode):
        req = ChatRequest(
            chatId=chat["id"],
            messages=[{"role": "user", "content": "hi"}],
            mode=mode,
            story={"world": "fantasy"} if mode == "story" else None,
        )
        return _state(req, me)

    story = state_for("story")
    assert story["memory"] is False and story["memories"] == []
    prompt = build_messages(
        "story", None, None, [], memory=story["memory"], memories=story["memories"],
        story=story["story"],
    )
    text = "\n".join(m["content"] for m in prompt)
    assert "nurse in Lahore" not in text and "## Memory" not in text
    tools = {t.name for t in _tools_for(ALL_TOOLS, True, True, story["memory"], story["story"])}
    assert not tools & MEMORY_TOOL_NAMES

    # Ordinary chat still gets them.
    chat_state = state_for("chat")
    assert chat_state["memory"] is True
    assert any("nurse in Lahore" in m["content"] for m in chat_state["memories"])


def test_story_setup_rejects_unknown_worlds(signed_in):
    bad = signed_in.post("/api/v1/chats", json={"mode": "story", "story": {"world": "moon"}})
    assert bad.status_code == 422


# ---------- character library REST ----------

def test_story_character_rest_roundtrip(signed_in):
    c = signed_in
    base = "/api/v1/story/characters"
    listing = c.get(base).json()
    assert listing["characters"] == [] and listing["limit"] > 0

    made = c.post(base, json={"name": "  Aria   Swift ", "description": "Elf", "voice": "Formal"})
    assert made.status_code == 201
    aria = made.json()
    assert aria["name"] == "Aria Swift"

    assert c.post(base, json={"name": "Pikachu"}).status_code == 201
    names = [ch["name"] for ch in c.get(base).json()["characters"]]
    assert names == ["Aria Swift", "Pikachu"]

    edited = c.patch(f"{base}/{aria['id']}", json={"name": "Aria", "description": "Ranger", "voice": ""})
    assert edited.json()["description"] == "Ranger"
    assert c.post(base, json={"name": "   "}).status_code == 400
    assert c.post(base, json={"name": "x" * 200}).status_code == 400
    assert c.post(base, json={"name": "Long", "voice": "x" * 5000}).status_code == 400

    assert c.delete(f"{base}/{aria['id']}").json() == {"ok": True}
    assert c.delete(f"{base}/{aria['id']}").status_code == 404
    assert c.patch(f"{base}/{aria['id']}", json={"name": "x"}).status_code == 404


def test_story_characters_are_private_and_capped(signed_in, monkeypatch):
    import app.main as main
    from app import db

    other = db.create_google_user("other@example.com", "Other", "google-sub-2")
    theirs = db.create_story_character(other["id"], "Secret", "", "")
    base = "/api/v1/story/characters"
    assert signed_in.get(base).json()["characters"] == []
    assert signed_in.patch(f"{base}/{theirs['id']}", json={"name": "Mine"}).status_code == 404
    assert signed_in.delete(f"{base}/{theirs['id']}").status_code == 404

    monkeypatch.setattr(main, "STORY_CHARACTER_MAX_PER_USER", 1)
    assert signed_in.post(base, json={"name": "One"}).status_code == 201
    assert signed_in.post(base, json={"name": "Two"}).status_code == 409


def test_lore_characters_lists_the_cast(signed_in):
    cast = signed_in.get("/api/v1/lore/characters").json()
    by_id = {c["id"]: c for c in cast}
    assert by_id["espressona-signora"]["name"] == "Espressona Signora"
    assert by_id["espressona-signora"]["hasLook"] is True
    assert {"id", "name", "blurb", "hasLook"} <= set(cast[0])
