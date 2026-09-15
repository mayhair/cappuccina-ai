import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from agent.graph import GRAPH
from agent.title import generate_title
from agent.tools import TOOL_DISPLAY_NAMES
from app.schemas import ChatRequest, ChatResponse, ToolCall

app = FastAPI(title="Cappuccina Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["POST"],
    allow_headers=["*"],
)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") for part in content if isinstance(part, dict)
    )


def _reasoning_text(content) -> str:
    """Human-readable reasoning summary from Responses API content blocks."""
    if isinstance(content, str):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "reasoning":
            for summary in block.get("summary", []):
                if isinstance(summary, dict) and summary.get("text"):
                    parts.append(summary["text"])
    return "".join(parts)


def _tool_label(name: str, args: dict) -> str:
    rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return f"{name}({rendered})"


def _tool_result_text(output) -> str:
    content = getattr(output, "content", output)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def _final_extras(messages) -> tuple[str, list[ToolCall]]:
    reasoning = ""
    results_by_id: dict[str, str] = {}
    pending: list[tuple[str, dict, str]] = []
    for msg in messages:
        content = getattr(msg, "content", "")
        reasoning += _reasoning_text(content)
        for call in getattr(msg, "tool_calls", None) or []:
            call_id = call.get("id") or ""
            pending.append((call.get("name", ""), call.get("args", {}), call_id))
        if msg.__class__.__name__ == "ToolMessage":
            results_by_id[getattr(msg, "tool_call_id", "") or ""] = _tool_result_text(
                getattr(msg, "content", "")
            )
    tool_calls = [
        ToolCall(
            name=name,
            display=TOOL_DISPLAY_NAMES.get(name, name),
            args=args,
            result=results_by_id.get(call_id, ""),
        )
        for name, args, call_id in pending
    ]
    return reasoning, tool_calls


def _state(req: ChatRequest) -> dict:
    return {
        "messages": [(m.role, m.content) for m in req.messages],
        "reasoning": req.reasoning,
        "mode": req.mode,
        "subject": req.subject,
    }


@app.post("/api/v1/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    result = await GRAPH.ainvoke(_state(req))
    messages = result["messages"]
    reasoning, tool_calls = _final_extras(messages)
    return ChatResponse(
        content=_text(messages[-1].content),
        reasoning=reasoning,
        tool_calls=tool_calls,
    )


@app.post("/api/v1/chat/stream")
async def chat_stream(req: ChatRequest):
    async def events():
        yield {"event": "start", "data": "{}"}
        final_text = ""
        async for event in GRAPH.astream_events(_state(req), version="v2"):
            kind = event["event"]
            if kind == "on_chat_model_start":
                final_text = ""
            elif kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                content = getattr(chunk, "content", "")
                text = _text(content)
                if text:
                    final_text += text
                    yield {"event": "token", "data": json.dumps({"content": text})}
                reasoning = _reasoning_text(content)
                if reasoning:
                    yield {
                        "event": "reasoning",
                        "data": json.dumps({"content": reasoning}),
                    }
            elif kind == "on_tool_start":
                call_id = event.get("run_id", "")
                args = event["data"].get("input", {}) or {}
                if not isinstance(args, dict):
                    args = {"input": args}
                name = event.get("name", "")
                yield {
                    "event": "tool_call",
                    "data": json.dumps(
                        {
                            "id": str(call_id),
                            "name": name,
                            "display": TOOL_DISPLAY_NAMES.get(name, name),
                            "args": args,
                            "label": _tool_label(name, args),
                        }
                    ),
                }
            elif kind == "on_tool_end":
                yield {
                    "event": "tool_result",
                    "data": json.dumps(
                        {
                            "id": str(event.get("run_id", "")),
                            "name": event.get("name", ""),
                            "content": _tool_result_text(
                                event["data"].get("output", "")
                            ),
                            "ok": True,
                        }
                    ),
                }
            elif kind == "on_tool_error":
                yield {
                    "event": "tool_result",
                    "data": json.dumps(
                        {
                            "id": str(event.get("run_id", "")),
                            "name": event.get("name", ""),
                            "content": str(event["data"].get("error", "")),
                            "ok": False,
                        }
                    ),
                }
        yield {"event": "done", "data": "{}"}
        if req.generate_title and req.messages:
            title = await generate_title(req.messages[0].content, final_text)
            if title:
                yield {"event": "title", "data": json.dumps({"title": title})}

    return EventSourceResponse(events(), ping=15)
