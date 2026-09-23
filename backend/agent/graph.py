from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from agent.model import make_model
from agent.prompts import build_system_prompt
from agent.tools import ALL_TOOLS


class AgentState(MessagesState):
    reasoning: str
    mode: str
    subject: str | None
    timezone: str | None


def call_model(state: AgentState):
    model = make_model(state["reasoning"], ALL_TOOLS)
    messages = [{"role": "system", "content": build_system_prompt(state["mode"], state.get("subject"), state.get("timezone"))}, *state["messages"]]
    return {"messages": [model.invoke(messages)]}


def should_continue(state: AgentState):
    # pyrefly: ignore [missing-attribute]
    if state["messages"][-1].tool_calls:
        return "tools"
    return END


def build_graph():
    # pyrefly: ignore [bad-specialization]
    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("tools", ToolNode(ALL_TOOLS))
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", should_continue, ["tools", END])
    graph.add_edge("tools", "call_model")
    return graph.compile()


GRAPH = build_graph()
