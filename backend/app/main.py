import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from agent.graph import GRAPH
from app.schemas import ChatRequest, ChatResponse

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
    return "".join(part.get("text", "") for part in content)


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
    return ChatResponse(content=_text(result["messages"][-1].content))


# ponytail: reasoner models may emit reasoning deltas that duplicate text tokens; front-end treats each token as append-only text, fine until duplicate output is observed
@app.post("/api/v1/chat/stream")
async def chat_stream(req: ChatRequest):
    async def events():
        yield {"event": "start", "data": "{}"}
        async for event in GRAPH.astream_events(_state(req), version="v2"):
            if event["event"] != "on_chat_model_stream":
                continue
            text = _text(event["data"]["chunk"].content)
            if text:
                yield {"event": "token", "data": json.dumps({"content": text})}
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(events(), ping=15)
