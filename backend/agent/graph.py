import logging
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from agent.fmhy import safety_note_for
from agent.model import make_model
from agent.prompts import build_messages
from agent import tools as agent_tools
from agent.tools import ALL_TOOLS, CALENDAR_TOOL_NAMES, GMAIL_TOOL_NAMES, MEMORY_TOOL_NAMES

logger = logging.getLogger(__name__)


class AgentState(MessagesState):
    reasoning: str
    mode: str
    subject: str | None
    timezone: str | None
    adult: bool
    gmail: bool
    calendar: bool
    gmail_enabled: bool
    calendar_enabled: bool
    memory: bool
    memories: list[dict]
    memory_total: int


def _tools_for(tool_list: list, gmail: bool, calendar: bool, memory: bool = True) -> list:
    """The tools the model is offered: Google tools only when that service is
    linked, memory tools only while the user has memory on."""
    hidden: set[str] = set()
    if not gmail:
        hidden |= GMAIL_TOOL_NAMES
    if not calendar:
        hidden |= CALENDAR_TOOL_NAMES
    if not memory:
        hidden |= MEMORY_TOOL_NAMES
    return [t for t in tool_list if t.name not in hidden]


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


# Tools whose success means the site's files may have changed.
WEBPAGE_CHANGE_TOOLS = frozenset({"write_webpage_file", "add_webpage_asset", "webpage_shell"})
# How many times one run may send the model back to fix what the automatic
# check found, so a page the model can't fix never loops forever.
AUTO_CHECK_LIMIT = 2

AUTO_CHECK_NOTE = """An automatic test of the webpage you just built found problems (the report is below). Fix them now: read_webpage_file, then write the corrected file back. Afterwards tell the user in a sentence or two what you fixed. If something can't be fixed, say so honestly instead of calling the page ready.

{report}"""


def _is_auto_check_note(message) -> bool:
    return bool(getattr(message, "additional_kwargs", {}).get("webpage_auto_check"))


def is_viewed_image_message(message) -> bool:
    """True for the injected message carrying pictures view_image fetched."""
    return bool(getattr(message, "additional_kwargs", {}).get("viewed_image"))


def viewed_images_message(batch):
    """A HumanMessage showing the pictures view_image fetched in ``batch``, or None.

    Tool results can only carry text through the Chat Completions API, so the
    picture rides in a user-role message placed right after the tool results.
    Later turns rebuild the same message from the saved view_image tool part
    (see app.main._viewed_image_message), so the picture stays in context.
    """
    viewed = []
    for message in batch:
        if getattr(message, "name", "") != agent_tools.VIEW_IMAGE_TOOL:
            continue
        artifact = getattr(message, "artifact", None) or {}
        if artifact.get("image_url"):
            viewed.append((artifact.get("image_id"), artifact["image_url"]))
    return viewed_images_message_for(viewed)


def viewed_images_message_for(viewed: list[tuple[str, str]]):
    """The vision message for ``(image_id, data_url)`` pairs, or None if empty."""
    blocks: list[dict] = []
    for image_id, url in viewed:
        blocks.append({"type": "image_url", "image_url": {"url": url}})
        blocks.append(
            {"type": "text", "text": f"[Generated image (id={image_id}), from view_image]"}
        )
    if not blocks:
        return None
    return HumanMessage(content=blocks, additional_kwargs={"viewed_image": True})


def _current_run(messages) -> list:
    """The messages after the user's latest turn — this run's own work."""
    index = len(messages)
    while index > 0 and (
        getattr(messages[index - 1], "type", "") != "human"
        or is_viewed_image_message(messages[index - 1])
    ):
        index -= 1
    return messages[index:]


def needs_auto_check(messages) -> bool:
    """True when this run changed the site after its last check.

    A check — the model's own check_webpage call or an automatic one — resets
    the flag; a successful write, asset or shell command sets it. Capped at
    AUTO_CHECK_LIMIT automatic rounds per run.
    """
    run = _current_run(messages)
    if sum(1 for m in run if _is_auto_check_note(m)) >= AUTO_CHECK_LIMIT:
        return False
    changed = False
    for message in run:
        if _is_auto_check_note(message):
            changed = False
        elif message.__class__.__name__ == "ToolMessage":
            if getattr(message, "status", "success") == "error":
                continue
            name = getattr(message, "name", "")
            if name in WEBPAGE_CHANGE_TOOLS:
                changed = True
            elif name == "check_webpage":
                changed = False
    return changed


def build_graph(tools=None):
    """Compile the agent graph around ``tools``.

    The tool list is fixed when the graph is built, so app startup loads the
    FMHY tools first and then builds the graph (see ``app.main.lifespan``).
    """
    tool_list = list(ALL_TOOLS if tools is None else tools)

    def call_model(state: AgentState):
        # Missing flags mean "linked" so callers that predate them (CLI, tests)
        # keep the full tool set.
        gmail = state.get("gmail", True)
        calendar = state.get("calendar", True)
        memory = state.get("memory", True)
        model = make_model(state["reasoning"], _tools_for(tool_list, gmail, calendar, memory))
        messages = build_messages(
            state["mode"],
            state.get("subject"),
            state.get("timezone"),
            state["messages"],
            state.get("adult", False),
            gmail=gmail,
            calendar=calendar,
            memory=memory,
            memories=state.get("memories"),
            memory_total=state.get("memory_total"),
            gmail_enabled=state.get("gmail_enabled", True),
            calendar_enabled=state.get("calendar_enabled", True),
        )
        return {"messages": [model.invoke(messages)]}

    def should_continue(state: AgentState):
        # pyrefly: ignore [missing-attribute]
        if state["messages"][-1].tool_calls:
            return "tools"
        if needs_auto_check(state["messages"]):
            return "auto_check"
        return END

    def auto_check(state: AgentState, config: RunnableConfig):
        """Test the site the model is about to call ready.

        The model often finishes without checking, so the run does it for
        her. Invoked as a tool call so the chat shows a "Testing Webpage"
        card. A clean page ends the run with no extra model call; problems go
        back to the model as a note so she fixes them before the reply ends.
        """
        call = {
            "name": "check_webpage",
            "args": {},
            "id": f"auto-check-{uuid4().hex}",
            "type": "tool_call",
        }
        try:
            result = agent_tools.check_webpage.invoke(call, config)
        except Exception:
            logger.exception("automatic webpage check failed")
            return {"messages": []}
        if (getattr(result, "artifact", None) or {}).get("status") != "problems":
            return {"messages": []}
        note = SystemMessage(
            content=AUTO_CHECK_NOTE.format(report=result.content),
            additional_kwargs={"webpage_auto_check": True},
        )
        return {"messages": [note]}

    def after_auto_check(state: AgentState):
        return "call_model" if _is_auto_check_note(state["messages"][-1]) else END

    def inject_fmhy_note(state: AgentState):
        """Add the ad-blocker note after FMHY tools run — never before.

        This node sits between the tools and the next model call, so the note
        lands in the transcript right after the tool results. It is added once
        per run, and only when an FMHY tool actually ran. Pictures fetched by
        view_image are attached here too, for the same reason: they must land
        right after the tool results.
        """
        messages = state["messages"]
        batch = _trailing_tool_messages(messages)
        note = safety_note_for(batch, messages)
        viewed = viewed_images_message(batch)
        return {"messages": [m for m in (note, viewed) if m is not None]}

    # pyrefly: ignore [bad-specialization]
    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("tools", ToolNode(tool_list, handle_tool_errors=_tool_error_message))
    graph.add_node("inject_fmhy_note", inject_fmhy_note)
    graph.add_node("auto_check", auto_check)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", should_continue, ["tools", "auto_check", END])
    graph.add_conditional_edges("auto_check", after_auto_check, ["call_model", END])
    graph.add_edge("tools", "inject_fmhy_note")
    graph.add_edge("inject_fmhy_note", "call_model")
    return graph.compile()


__all__ = ["AgentState", "build_graph"]
