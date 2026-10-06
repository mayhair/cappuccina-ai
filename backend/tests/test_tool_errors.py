"""A failing tool must not kill the run.

LangGraph's default handler re-raises anything that is not a bad-arguments
error, so one tool bug used to take the whole SSE stream with it: the reply was
lost and uvicorn logged an ASGI traceback (the sqlite misuse from parallel
tool calls arrived exactly this way). The agent graph now hands the failure
back to the model as an error ToolMessage.
"""

import json

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

import agent.graph as graph_module


@tool
def boom(reason: str) -> str:
    """Always fails."""
    raise RuntimeError(f"kaboom: {reason}")


class _FakeModel:
    """Calls boom once, then answers — the run continues without an API key."""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[{"id": "t1", "name": "boom", "args": {"reason": "x"}}],
            )
        return AIMessage(content="Handled the failure.")


def _run_graph(monkeypatch):
    model = _FakeModel()
    monkeypatch.setattr(graph_module, "make_model", lambda *args, **kwargs: model)
    graph = graph_module.build_graph([boom])
    out = graph.invoke(
        {
            "messages": [HumanMessage(content="hi")],
            "reasoning": "low",
            "mode": "chat",
            "subject": None,
            "timezone": None,
        }
    )
    return model, out


def test_a_raising_tool_becomes_an_error_message_the_model_can_read(monkeypatch):
    model, out = _run_graph(monkeypatch)

    tool_message = next(
        message
        for message in out["messages"]
        if message.__class__.__name__ == "ToolMessage"
    )
    assert tool_message.status == "error"
    assert "boom" in str(tool_message.content)
    assert "kaboom" in str(tool_message.content)
    # The model was called again with the error in the transcript.
    assert model.calls == 2
    assert out["messages"][-1].content == "Handled the failure."


def _sse_events(text):
    events = {}
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if event:
            events.setdefault(event, []).append(json.loads(data) if data else {})
    return events


def test_the_stream_reports_a_failed_tool_as_not_ok(signed_in, monkeypatch):
    """The UI must not present a caught tool failure as a success."""
    from types import SimpleNamespace

    from langchain_core.messages import ToolMessage

    import app.main as main

    class FakeGraph:
        async def astream_events(self, state, config=None, version=None):
            yield {"event": "on_chat_model_start", "data": {}}
            yield {
                "event": "on_tool_start",
                "run_id": "r1",
                "name": "web_search",
                "data": {"input": {"query": "cats"}},
            }
            yield {
                "event": "on_tool_end",
                "run_id": "r1",
                "name": "web_search",
                "data": {
                    "output": ToolMessage(
                        content="Tool error: RuntimeError: boom",
                        tool_call_id="t1",
                        status="error",
                    )
                },
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="Sorry about that.")},
            }

    monkeypatch.setattr(main, "GRAPH", FakeGraph())
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    res = signed_in.post(
        "/api/v1/chat/stream",
        json={
            "chatId": chat["id"],
            "messages": [{"role": "user", "content": "hi"}],
            "generateTitle": False,
        },
    )
    assert res.status_code == 200
    result = _sse_events(res.text)["tool_result"][0]
    assert result["ok"] is False
    assert "boom" in result["content"]

    # The stored card agrees, so a reload does not show a green tool call.
    stored = signed_in.get("/api/v1/chats").json()
    tool_parts = [
        p for p in stored[0]["messages"][-1]["parts"] if p.get("type") == "tool"
    ]
    assert tool_parts[0]["ok"] is False
