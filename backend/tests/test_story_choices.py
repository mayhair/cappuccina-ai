"""Suggested next moves in story mode.

Models tend to finish a story turn without calling suggest_actions, so the
graph asks for the moves itself with one extra, forced call. These tests run
the real graph against a fake model.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agent.graph as graph_module
from agent.tools import suggest_actions


class _FakeModel:
    """Writes the story turn, then answers the forced next-moves call."""

    def __init__(self, reply: str = "The stranger slides the map across the table.") -> None:
        self.reply = reply
        self.calls = 0
        self.forced = False

    def bind_tools(self, tools, tool_choice=None):
        self.forced = tool_choice == "suggest_actions"
        return self

    def with_config(self, **kwargs):
        assert graph_module.STORY_CHOICES_TAG in kwargs.get("tags", [])
        return self

    def invoke(self, messages):
        self.calls += 1
        if self.forced:
            return AIMessage(
                content="ignored text",
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "suggest_actions",
                        "args": {"options": ["Take the map", "Ask who sent them"]},
                    }
                ],
            )
        return AIMessage(content=self.reply)


def _run(monkeypatch, *, choices=True, mode="story", reply=None):
    model = _FakeModel(reply) if reply else _FakeModel()
    monkeypatch.setattr(graph_module, "make_model", lambda *args, **kwargs: model)
    graph = graph_module.build_graph([suggest_actions])
    out = graph.invoke(
        {
            "messages": [HumanMessage(content="Let's start the story")],
            "reasoning": "low",
            "mode": mode,
            "subject": None,
            "timezone": None,
            "story": {"world": "fantasy"},
            "story_choices": choices,
        }
    )
    return model, out["messages"]


def test_a_story_turn_without_moves_gets_them_from_one_extra_call(monkeypatch):
    model, messages = _run(monkeypatch)
    assert model.calls == 2
    reply, asked, result = messages[1:]
    assert reply.content.startswith("The stranger")
    # The forced call's own text never becomes part of the reply.
    assert asked.content == "" and asked.tool_calls[0]["name"] == "suggest_actions"
    assert isinstance(result, ToolMessage) and "Ask who sent them" in result.content


def test_no_extra_call_when_the_setting_is_off_or_outside_story_mode(monkeypatch):
    model, messages = _run(monkeypatch, choices=False)
    assert model.calls == 1 and len(messages) == 2
    model, messages = _run(monkeypatch, mode="chat")
    assert model.calls == 1 and len(messages) == 2


def test_out_of_character_replies_get_no_moves(monkeypatch):
    model, messages = _run(monkeypatch, reply="(ooc) Sure, we can slow down.")
    assert model.calls == 1 and len(messages) == 2


def test_moves_the_model_offered_itself_end_the_turn():
    state = {
        "mode": "story",
        "story_choices": True,
        "messages": [
            HumanMessage(content="go"),
            AIMessage(
                content="The door creaks.",
                tool_calls=[{"id": "c1", "name": "suggest_actions", "args": {"options": ["Run"]}}],
            ),
            ToolMessage(content="{}", tool_call_id="c1", name="suggest_actions"),
            AIMessage(content="More text."),
        ],
    }
    assert not graph_module.needs_story_choices(state)
    assert graph_module._only_story_tools(state["messages"][2:3])


def test_the_stored_reply_is_the_story_text_not_the_moves():
    from app.main import _reply_text

    messages = [
        HumanMessage(content="go"),
        AIMessage(content="The door creaks."),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "suggest_actions", "args": {"options": ["Run"]}}]),
        ToolMessage(content='{"status": "story_choices"}', tool_call_id="c1", name="suggest_actions"),
    ]
    assert _reply_text(messages) == "The door creaks."
