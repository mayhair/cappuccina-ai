import logging

from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from agent.fmhy import safety_note_for
from agent.model import make_model
from agent.prompts import build_messages
from agent.tools import ALL_TOOLS

logger = logging.getLogger(__name__)


class AgentState(MessagesState):
    reasoning: str
    mode: str
    subject: str | None
    timezone: str | None


def _tool_error_message(error: Exception) -> str:
    """Hand a tool failure back to the model instead of killing the run.

    LangGraph's default handler re-raises anything that is not a bad-arguments
    error, so one tool bug used to tear down the whole SSE stream: the reply
    was lost, nothing was stored, and uvicorn logged an ASGI traceback. Here
    the model gets the message, can apologize or route around it, and the tool
    card shows the failure (the stream marks it not-ok from the ToolMessage's
    status). The traceback still goes to the server log.
    """
    logger.exception("tool call failed")
    return f"Tool error: {type(error).__name__}: {error}"


def _trailing_tool_messages(messages) -> list:
    """The ToolMessages at the tail of the history — the batch just executed."""
    index = len(messages)
    while index > 0 and messages[index - 1].__class__.__name__ == "ToolMessage":
        index -= 1
    return messages[index:]


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

    def inject_fmhy_note(state: AgentState):
        """Add the ad-blocker note after FMHY tools run — never before.

        This node sits between the tools and the next model call, so the note
        lands in the transcript right after the tool results. It is added once
        per run, and only when an FMHY tool actually ran.
        """
        messages = state["messages"]
        note = safety_note_for(_trailing_tool_messages(messages), messages)
        return {"messages": [note] if note is not None else []}

    # pyrefly: ignore [bad-specialization]
    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("tools", ToolNode(tool_list, handle_tool_errors=_tool_error_message))
    graph.add_node("inject_fmhy_note", inject_fmhy_note)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", should_continue, ["tools", END])
    graph.add_edge("tools", "inject_fmhy_note")
    graph.add_edge("inject_fmhy_note", "call_model")
    return graph.compile()


__all__ = ["AgentState", "build_graph"]
