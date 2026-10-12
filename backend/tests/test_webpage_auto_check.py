"""The graph tests a site the model changed before the run may end."""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

import agent.graph as G
import agent.tools as T


class _FakeModel:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls += 1
        return self._script.pop(0)


def _scripted_model(script, monkeypatch) -> _FakeModel:
    model = _FakeModel(script)
    monkeypatch.setattr(G, "make_model", lambda reasoning, tools: model)
    return model


def _fake_checks(results, monkeypatch) -> list:
    """Each automatic check returns the next status; the calls are recorded."""
    calls = []

    def run(config, steps=None):
        status = results[len(calls)]
        calls.append(status)
        return f"report: {status}", {"status": status}

    monkeypatch.setattr(T, "run_webpage_check", run)
    return calls


@tool
def write_webpage_file(path: str, content: str) -> str:
    """Write."""
    return '{"status": "webpage_file_written"}'


@tool
def web_search(query: str) -> str:
    """Search."""
    return "result"


def _write(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_webpage_file",
                "args": {"path": "index.html", "content": "<h1>hi</h1>"},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _run(tools=(write_webpage_file,)):
    graph = G.build_graph(list(tools))
    state = {
        "messages": [HumanMessage(content="make me a page")],
        "reasoning": "low",
        "mode": "chat",
        "subject": None,
        "timezone": "UTC",
    }
    return asyncio.run(graph.ainvoke(state))


def _notes(out) -> list:
    return [m for m in out["messages"] if m.additional_kwargs.get("webpage_auto_check")]


def test_a_clean_page_ends_without_another_model_call(monkeypatch):
    model = _scripted_model([_write("1"), AIMessage(content="It's ready!")], monkeypatch)
    calls = _fake_checks(["clean"], monkeypatch)
    out = _run()
    assert calls == ["clean"]
    assert model.calls == 2
    assert _notes(out) == []
    assert out["messages"][-1].content == "It's ready!"


def test_problems_send_the_model_back_to_fix_them(monkeypatch):
    model = _scripted_model(
        [_write("1"), AIMessage(content="ready!"), _write("2"), AIMessage(content="fixed it")],
        monkeypatch,
    )
    calls = _fake_checks(["problems", "clean"], monkeypatch)
    out = _run()
    assert calls == ["problems", "clean"]
    assert model.calls == 4
    notes = _notes(out)
    assert len(notes) == 1 and "report: problems" in notes[0].content
    assert out["messages"][-1].content == "fixed it"


def test_automatic_rounds_are_capped(monkeypatch):
    script = []
    for n in range(G.AUTO_CHECK_LIMIT + 1):
        script += [_write(str(n)), AIMessage(content=f"attempt {n}")]
    _scripted_model(script, monkeypatch)
    calls = _fake_checks(["problems"] * 5, monkeypatch)
    out = _run()
    assert len(calls) == G.AUTO_CHECK_LIMIT
    assert len(_notes(out)) == G.AUTO_CHECK_LIMIT


def test_no_check_when_the_site_did_not_change(monkeypatch):
    _scripted_model(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "web_search", "args": {"query": "x"}, "id": "1", "type": "tool_call"}],
            ),
            AIMessage(content="here you go"),
        ],
        monkeypatch,
    )
    calls = _fake_checks([], monkeypatch)
    _run(tools=(write_webpage_file, web_search))
    assert calls == []


def test_an_unavailable_checker_ends_the_run(monkeypatch):
    model = _scripted_model([_write("1"), AIMessage(content="ready")], monkeypatch)
    _fake_checks(["unavailable"], monkeypatch)
    out = _run()
    assert model.calls == 2
    assert _notes(out) == []


def test_the_models_own_check_counts():
    human = HumanMessage(content="hi")
    wrote = ToolMessage(content="ok", tool_call_id="1", name="write_webpage_file")
    checked = ToolMessage(content="ok", tool_call_id="2", name="check_webpage")
    failed = ToolMessage(content="boom", tool_call_id="3", name="write_webpage_file", status="error")
    assert G.needs_auto_check([human, wrote])
    assert not G.needs_auto_check([human, wrote, checked])
    assert not G.needs_auto_check([human, failed])
    # Only the current run counts: an earlier turn's writes are not re-checked.
    assert not G.needs_auto_check([wrote, human])
