from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from agent.fmhy import safety_note_for
from agent.model import make_model
from agent.prompts import build_messages
from agent.tools import (
    ALL_TOOLS,
    image_id_from_tool_result,
    image_url_block,
)
from app import db


class AgentState(MessagesState):
    reasoning: str
    mode: str
    subject: str | None
    timezone: str | None


def _trailing_tool_messages(messages) -> list:
    """The ToolMessages at the tail of the history — the batch just executed."""
    index = len(messages)
    while index > 0 and messages[index - 1].__class__.__name__ == "ToolMessage":
        index -= 1
    return messages[index:]


def _tool_text(content) -> str:
    """Flatten a tool result (content blocks or a string) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def _image_messages(batch, user_id: str) -> list:
    """A synthetic user turn showing pictures the tools just made, or [].

    The model can only receive images in a user message, so each freshly
    generated/edited picture is re-emitted here with its id. Empty when the
    batch made no pictures (or the saved bytes are gone).
    """
    blocks: list[dict] = []
    for msg in batch:
        image_id = image_id_from_tool_result(
            getattr(msg, "name", ""), _tool_text(getattr(msg, "content", ""))
        )
        if not image_id:
            continue
        found = db.read_image_bytes(user_id, image_id)
        if found is None:
            continue
        raw, mime = found
        blocks.append(image_url_block(mime, raw))
        blocks.append(
            {"type": "text", "text": f"[Picture you just made (id={image_id})]"}
        )
    if not blocks:
        return []
    return [
        HumanMessage(
            content=[
                {"type": "text", "text": "Here is the picture you just made."},
                *blocks,
            ],
            additional_kwargs={"generated_image_context": True},
        )
    ]


def post_tool_messages(batch, existing, user_id: str) -> list:
    """Messages to append after a tool batch: made pictures, then the FMHY note.

    ``batch`` is snapshotted by the caller before anything is appended: the FMHY
    note only fires when an FMHY tool ran and inspects the trailing ToolMessages,
    which the injected picture message would otherwise hide.
    """
    appended = _image_messages(batch, user_id) if user_id else []
    note = safety_note_for(batch, existing)
    if note is not None:
        appended.append(note)
    return appended


def build_graph(tools=None):
    """Compile the agent graph around ``tools``.

    The tool list is fixed when the graph is built, so app startup loads the
    FMHY tools first and then builds the graph (see ``app.main.lifespan``).
    """
    tool_list = list(ALL_TOOLS if tools is None else tools)

    def call_model(state: AgentState):
        model = make_model(state["reasoning"], tool_list)
        messages = build_messages(
            state["mode"], state.get("subject"), state.get("timezone"), state["messages"]
        )
        return {"messages": [model.invoke(messages)]}

    def should_continue(state: AgentState):
        # pyrefly: ignore [missing-attribute]
        if state["messages"][-1].tool_calls:
            return "tools"
        return END

    def after_tools(state: AgentState, config: RunnableConfig):
        """Show the model the pictures it just made, then add the FMHY note."""
        messages = state["messages"]
        batch = _trailing_tool_messages(messages)
        user_id = ((config or {}).get("configurable", {}) or {}).get("user_id", "")
        return {"messages": post_tool_messages(batch, messages, user_id)}

    # pyrefly: ignore [bad-specialization]
    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("tools", ToolNode(tool_list))
    graph.add_node("after_tools", after_tools)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", should_continue, ["tools", END])
    graph.add_edge("tools", "after_tools")
    graph.add_edge("after_tools", "call_model")
    return graph.compile()


__all__ = ["AgentState", "build_graph"]
