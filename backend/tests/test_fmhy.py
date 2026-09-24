import asyncio

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from agent import fmhy
from agent import graph as G


# ---------- NSFW filtering ----------


def test_strip_nsfw_drops_tagged_bullet_lines():
    text = "\n".join(
        [
            "* [Cineby](https://cineby.gdn/) - Movies / TV / Anime",
            "* [wallhaven.cc](https://wallhaven.cc/) - Wallpapers / Some NSFW",
            "* [OBS Studio](https://obsproject.com/) - Live Stream Manager",
        ]
    )
    out = fmhy.strip_nsfw(text)
    assert "Cineby" in out
    assert "OBS Studio" in out
    assert "wallhaven" not in out


def test_strip_nsfw_drops_whole_numbered_record():
    text = "\n".join(
        [
            "1. [section] Good Stuff",
            "   Snippet: a nice entry",
            "   Links: [Good](https://good.example/)",
            "",
            "2. [section] Bad Stuff",
            "   Snippet: an entry tagged Some NSFW",
            "   Links: [Bad](https://bad.example/)",
        ]
    )
    out = fmhy.strip_nsfw(text)
    assert "Good" in out
    assert "good.example" in out
    assert "bad.example" not in out
    assert "Bad Stuff" not in out


def test_strip_nsfw_keeps_porn_quitting_heading():
    """The one 'porn' heading in the dataset is recovery content, not adult links."""
    text = "## \u25b7 Porn Quitting\n* [EasyPeasy](https://easypeasymethod.org/) - quit guide"
    assert "Porn Quitting" in fmhy.strip_nsfw(text)


# ---------- safety-note injection ----------


def _tool_message(name: str, content: str = "ok") -> object:
    from langchain_core.messages import ToolMessage

    return ToolMessage(content=content, name=name, tool_call_id="1")


def test_safety_note_added_after_fmhy_tool():
    note = fmhy.safety_note_for([_tool_message("fmhy_search")], [])
    assert note is not None
    assert note.__class__.__name__ == "SystemMessage"
    assert note.additional_kwargs.get("fmhy_safety_note") is True
    assert "ad-blocker" in note.content
    assert "AdGuard" in note.content and "Brave" in note.content


def test_safety_note_not_added_for_other_tools():
    assert fmhy.safety_note_for([_tool_message("web_search")], []) is None


def test_safety_note_not_duplicated_within_a_run():
    existing = [SystemMessage(content="x", additional_kwargs={"fmhy_safety_note": True})]
    assert fmhy.safety_note_for([_tool_message("fmhy_get_links")], existing) is None


# ---------- graph-level injection ----------


class _FakeModel:
    def __init__(self, script):
        self._script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self._script.pop(0)


def _scripted_model(script, monkeypatch):
    # One instance shared across calls: call_model asks for a fresh model each
    # turn, so the script has to live outside the factory to advance.
    model = _FakeModel(script)
    monkeypatch.setattr(G, "make_model", lambda reasoning, tools: model)


def _initial_state(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "reasoning": "low",
        "mode": "chat",
        "subject": None,
        "timezone": "UTC",
    }


def _tool_call(name: str, call_id: str) -> dict:
    return {"name": name, "args": {"query": "x"}, "id": call_id, "type": "tool_call"}


def _run_graph(tools):
    graph = G.build_graph(tools)
    return asyncio.run(graph.ainvoke(_initial_state("where can I watch it free")))


def _fmhy_tool():
    @tool
    async def fmhy_search(query: str) -> str:
        """Search."""
        return "1. [section] Good"

    return fmhy_search


def _other_tool():
    @tool
    async def web_search(query: str) -> str:
        """Search."""
        return "web result"

    return web_search


def test_graph_injects_note_after_fmhy_tool(monkeypatch):
    _scripted_model(
        [
            AIMessage(content="", tool_calls=[_tool_call("fmhy_search", "1")]),
            AIMessage(content="here are the links"),
        ],
        monkeypatch,
    )
    out = _run_graph([_fmhy_tool()])
    kinds = [m.__class__.__name__ for m in out["messages"]]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "SystemMessage", "AIMessage"]
    note = out["messages"][-2]
    assert "ad-blocker" in note.content
    assert "AdGuard" in note.content and "Brave" in note.content


def test_graph_does_not_inject_for_other_tools(monkeypatch):
    _scripted_model(
        [
            AIMessage(content="", tool_calls=[_tool_call("web_search", "1")]),
            AIMessage(content="done"),
        ],
        monkeypatch,
    )
    out = _run_graph([_other_tool()])
    kinds = [m.__class__.__name__ for m in out["messages"]]
    assert "SystemMessage" not in kinds


def test_graph_injects_note_only_once_per_run(monkeypatch):
    _scripted_model(
        [
            AIMessage(content="", tool_calls=[_tool_call("fmhy_search", "1")]),
            AIMessage(content="", tool_calls=[_tool_call("fmhy_search", "2")]),
            AIMessage(content="done"),
        ],
        monkeypatch,
    )
    out = _run_graph([_fmhy_tool()])
    notes = [
        m
        for m in out["messages"]
        if m.__class__.__name__ == "SystemMessage" and m.additional_kwargs.get("fmhy_safety_note")
    ]
    assert len(notes) == 1



# ---------- media-sites lookup (the "where can I watch X" path) ----------


class _RecordingTool:
    """Stands in for a discovered upstream tool, recording the args it saw."""

    def __init__(self, result="* Streaming: Cinejoy / Movy"):
        self.result = result
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        value = self.result(args) if callable(self.result) else self.result
        return [{"type": "text", "text": value}]


def _upstream(monkeypatch, result="* Streaming: Cinejoy / Movy", **extra):
    rec = _RecordingTool(result)
    originals = {"fmhy_get_section": rec}
    originals.update(extra)
    monkeypatch.setattr(fmhy, "_originals", originals)
    return rec


_HEADING_ONLY = "# ▷ Nothing here\n\n***"


def test_media_sites_reads_the_full_sections_not_the_beginner_block(monkeypatch):
    rec = _upstream(monkeypatch, "* 🌟 **[Cinejoy](https://cinejoy.pk/)** - Movies / TV")
    out = asyncio.run(
        fmhy.fmhy_media_sites.ainvoke(
            {"media_kind": "movies_shows", "title": "KPop Demon Hunters"}
        )
    )
    plan = fmhy._MEDIA_PLAN["movies_shows"]
    asked = [c["title_or_slug"] for c in rec.calls]
    assert asked == [*plan["core"], *plan["niche"]]
    assert plan["beginner"] not in asked
    assert all(c["max_chars"] == fmhy._SECTION_MAX_CHARS for c in rec.calls)
    # Stream Aggregators alone is 9517 chars; a lower cap silently truncates it.
    assert fmhy._SECTION_MAX_CHARS >= 9517
    assert "Cinejoy" in out
    assert "KPop Demon Hunters" in out
    assert fmhy._NOT_A_CATALOG in out


def test_media_sites_never_sends_the_title_upstream(monkeypatch):
    rec = _upstream(monkeypatch)
    asyncio.run(
        fmhy.fmhy_media_sites.ainvoke(
            {"media_kind": "anime", "title": "Frieren: Beyond Journey's End"}
        )
    )
    plan = fmhy._MEDIA_PLAN["anime"]
    assert rec.calls == [
        {"title_or_slug": name, "max_chars": fmhy._SECTION_MAX_CHARS}
        for name in [*plan["core"], *plan["niche"]]
    ]


def test_media_sites_kinds_map_to_full_sections(monkeypatch):
    rec = _upstream(monkeypatch, "* [x](https://x.example/)")
    for kind, plan in fmhy._MEDIA_PLAN.items():
        rec.calls.clear()
        asyncio.run(fmhy.fmhy_media_sites.ainvoke({"media_kind": kind}))
        asked = [c["title_or_slug"] for c in rec.calls]
        assert asked == [*plan["core"], *plan["niche"]]
        assert any(name in plan["core"] for name in asked)


def test_media_sites_skips_heading_only_sections(monkeypatch):
    def body(args):
        if args["title_or_slug"] == "Game Repacks":
            return _HEADING_ONLY
        return "* [Good](https://good.example/)"

    _upstream(monkeypatch, body)
    out = asyncio.run(fmhy.fmhy_media_sites.ainvoke({"media_kind": "games"}))
    assert "good.example" in out
    assert "Nothing here" not in out


def test_arrange_puts_starred_entries_first():
    text = "\n".join(
        [
            "# ▷ Sites",
            "* **Note** - a note is not an entry",
            "* [Plain](https://p.example/)",
            "* ⭐ **[Starred](https://s.example/)**",
            "* 🌟 **[Gold](https://g.example/)**",
        ]
    )
    out, dropped = fmhy._arrange(text, cap=40)
    assert [line for line in out.splitlines() if fmhy._is_entry_bullet(line)] == [
        "* 🌟 **[Gold](https://g.example/)**",
        "* ⭐ **[Starred](https://s.example/)**",
        "* [Plain](https://p.example/)",
    ]
    assert dropped == 0
    assert "a note is not an entry" in out


def test_arrange_caps_entries_and_counts_the_rest():
    text = "\n".join(f"* [S{i}](https://s{i}.example/)" for i in range(50))
    out, dropped = fmhy._arrange(text, cap=10)
    assert len([line for line in out.splitlines() if fmhy._is_entry_bullet(line)]) == 10
    assert dropped == 40


def test_fit_drops_tail_bullets_until_it_fits():
    text = "\n".join(f"* [S{i}](https://s{i}.example/)" for i in range(20))
    fitted, dropped = fmhy._fit(text, budget=200)
    assert len(fitted) <= 200
    assert dropped > 0
    assert "S0" in fitted


def test_media_sites_falls_back_to_the_search_resolver(monkeypatch):
    def body(args):
        if args["title_or_slug"] == "▷ Stream Aggregators":
            return "* 🌟 **[Cinejoy](https://cinejoy.pk/)** - Movies"
        return _HEADING_ONLY

    search = _RecordingTool(
        "\n".join(
            [
                "1. [section] ▷ Stream Aggregators",
                "   Section: ▷ Stream Aggregators",
                "   Snippet: lots of movie sites",
                "   Links: [X](https://x.example/)",
            ]
        )
    )
    rec = _upstream(monkeypatch, body, fmhy_search=search)
    out = asyncio.run(fmhy.fmhy_media_sites.ainvoke({"media_kind": "movies_shows"}))
    assert "Cinejoy" in out
    assert any(c["title_or_slug"] == "▷ Stream Aggregators" for c in rec.calls)
    assert search.calls[0]["query"] == fmhy._MEDIA_PLAN["movies_shows"]["resolver"]


def test_media_sites_falls_back_to_the_beginner_block(monkeypatch):
    def body(args):
        if args["title_or_slug"] == "Movies / Shows":
            return "* **Streaming: [NEPU](https://nepu.io/) / [Cinejoy](https://cinejoy.pk/)**"
        return _HEADING_ONLY

    search = _RecordingTool(
        "\n".join(
            [
                "1. [entry] Nothing useful",
                "   Section: ▷ Somewhere",
                "   Snippet: x",
                "   Links: [Y](https://y.example/)",
            ]
        )
    )
    rec = _upstream(monkeypatch, body, fmhy_search=search)
    out = asyncio.run(fmhy.fmhy_media_sites.ainvoke({"media_kind": "movies_shows"}))
    assert "NEPU" in out
    assert "Short overview" in out
    assert rec.calls[-1]["title_or_slug"] == "Movies / Shows"


def test_media_sites_kind_is_an_enum():
    args = fmhy.fmhy_media_sites.args_schema
    schema = args.model_json_schema() if hasattr(args, "model_json_schema") else args.schema()
    assert set(schema["properties"]["media_kind"]["enum"]) == set(fmhy._MEDIA_PLAN)


def test_media_sites_rejects_an_unknown_kind(monkeypatch):
    import pytest

    _upstream(monkeypatch)
    with pytest.raises(Exception):
        asyncio.run(fmhy.fmhy_media_sites.ainvoke({"media_kind": "podcasts"}))


# ---------- cache refresh ----------


def test_refresh_dataset_calls_upstream(monkeypatch):
    rec = _RecordingTool("ok")
    monkeypatch.setattr(fmhy, "_originals", {"fmhy_refresh_cache": rec})
    asyncio.run(fmhy._refresh_dataset())
    assert rec.calls == [{}]


def test_refresh_dataset_swallows_errors(monkeypatch):
    class _Boom:
        async def ainvoke(self, args):
            raise RuntimeError("cache on fire")

    monkeypatch.setattr(fmhy, "_originals", {"fmhy_refresh_cache": _Boom()})
    asyncio.run(fmhy._refresh_dataset())


def test_refresh_dataset_noop_without_the_tool(monkeypatch):
    monkeypatch.setattr(fmhy, "_originals", {})
    asyncio.run(fmhy._refresh_dataset())


# ---------- title-query guards on the free-form search tools ----------


def test_search_tools_warn_against_title_queries():
    for wrapper in [fmhy.fmhy_search, fmhy.fmhy_get_links]:
        description = (wrapper.description or "").lower()
        assert "never titles" in description
        assert "fmhy_media_sites" in description
    assert "never fmhy_search" in (fmhy.fmhy_media_sites.description or "").lower()


def test_search_results_carry_the_no_title_caveat(monkeypatch):
    monkeypatch.setattr(fmhy, "_originals", {"fmhy_search": _RecordingTool("1. stuff")})
    out = asyncio.run(fmhy.fmhy_search.ainvoke({"query": "fonts"}))
    assert fmhy._SEARCH_CAVEAT in out
    assert "1. stuff" in out


def test_get_links_results_carry_the_no_title_caveat(monkeypatch):
    monkeypatch.setattr(fmhy, "_originals", {"fmhy_get_links": _RecordingTool("1. stuff")})
    out = asyncio.run(fmhy.fmhy_get_links.ainvoke({"query": "fonts"}))
    assert fmhy._SEARCH_CAVEAT in out


def test_safety_note_added_for_media_sites():
    note = fmhy.safety_note_for([_tool_message("fmhy_media_sites")], [])
    assert note is not None


# ---------- loader behaviour ----------


def test_load_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setattr(fmhy, "FMHY_MCP_URL", "")
    assert asyncio.run(fmhy.load_fmhy_tools()) == []


def test_load_discovers_wrappers_and_refreshes_the_cache(monkeypatch):
    rec = _RecordingTool("ok")
    rec.name = "fmhy_refresh_cache"
    upstream = [rec]
    for name in sorted(fmhy._UPSTREAM_TOOL_NAMES):
        stub = _RecordingTool("x")
        stub.name = name
        upstream.append(stub)

    class _FakeAdapter:
        def __init__(self, url):
            self.url = url

        async def list_tools(self):
            return upstream

    monkeypatch.setattr("langchain.mcp.MCPAdapter", _FakeAdapter)
    monkeypatch.setattr(fmhy, "_originals", {})
    wrappers = asyncio.run(fmhy.load_fmhy_tools())
    assert {wrapper.name for wrapper in wrappers} == set(fmhy.FMHY_TOOL_NAMES)
    assert rec.calls == [{}]


def test_wrapper_is_friendly_when_not_loaded(monkeypatch):
    monkeypatch.setattr(fmhy, "_originals", {})
    out = asyncio.run(fmhy.fmhy_search.ainvoke({"query": "x"}))
    assert "isn't available" in out


def test_descriptions_carry_the_gating_rule():
    for wrapper in fmhy._all_wrappers():
        assert "insisted" in (wrapper.description or "")


# ---------- non-leak into persisted output ----------


def test_final_extras_ignores_system_messages():
    from app.main import _final_extras

    messages = [
        HumanMessage(content="where can I watch it free"),
        AIMessage(content="here you go"),
        SystemMessage(content="SECRET SAFETY NOTE", additional_kwargs={"fmhy_safety_note": True}),
    ]
    _reasoning, _tool_calls, parts = _final_extras(messages)
    texts = [part["text"] for part in parts if part.get("type") == "text"]
    assert "SECRET SAFETY NOTE" not in texts
    assert any("here you go" in t for t in texts)


# ---------- UI labels ----------


def test_fmhy_display_names_registered():
    from agent.tools import TOOL_DISPLAY_NAMES

    assert TOOL_DISPLAY_NAMES["fmhy_media_sites"] == "Free Media Sites"
    assert TOOL_DISPLAY_NAMES["fmhy_search"] == "Free Media Search"
    assert TOOL_DISPLAY_NAMES["fmhy_get_links"] == "Free Media Links"
