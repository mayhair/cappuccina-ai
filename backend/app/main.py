import json

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from agent.graph import GRAPH
from agent.title import generate_title
from agent.tools import TOOL_DISPLAY_NAMES
from app import db
from app.auth import (
    clear_session_cookie,
    current_user,
    hash_password,
    session_token,
    set_session_cookie,
    verify_password,
)
from app.schemas import (
    ChangePasswordRequest,
    ChatRequest,
    ChatResponse,
    CreateChatRequest,
    LoginRequest,
    RegisterRequest,
    RenameChatRequest,
    ToolCall,
    UpdateProfileRequest,
    UserResponse,
)

app = FastAPI(title="Cappuccina Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
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


def _final_extras(messages) -> tuple[str, list[ToolCall], list[dict]]:
    """Reasoning, tool calls, and the ordered interleaving of the two."""
    reasoning = ""
    results_by_id: dict[str, str] = {}
    pending: list[tuple[str, dict, str]] = []
    parts: list[dict] = []
    tools_by_part_id: dict[str, dict] = {}
    for msg in messages:
        content = getattr(msg, "content", "")
        step_reasoning = _reasoning_text(content)
        if step_reasoning:
            reasoning += step_reasoning
            parts.append({"type": "reasoning", "text": step_reasoning})
        for call in getattr(msg, "tool_calls", None) or []:
            call_id = call.get("id") or ""
            name = call.get("name", "")
            args = call.get("args", {})
            pending.append((name, args, call_id))
            part = {
                "type": "tool",
                "id": call_id,
                "name": name,
                "display": TOOL_DISPLAY_NAMES.get(name, name),
                "args": args,
                "result": "",
                "ok": True,
            }
            parts.append(part)
            if call_id:
                tools_by_part_id[call_id] = part
        if msg.__class__.__name__ == "ToolMessage":
            results_by_id[getattr(msg, "tool_call_id", "") or ""] = _tool_result_text(
                getattr(msg, "content", "")
            )
            part = tools_by_part_id.get(getattr(msg, "tool_call_id", "") or "")
            if part is not None:
                part["result"] = _tool_result_text(getattr(msg, "content", ""))
    tool_calls = [
        ToolCall(
            name=name,
            display=TOOL_DISPLAY_NAMES.get(name, name),
            args=args,
            result=results_by_id.get(call_id, ""),
        )
        for name, args, call_id in pending
    ]
    return reasoning, tool_calls, parts


def _state(req: ChatRequest) -> dict:
    return {
        "messages": [(m.role, m.content) for m in req.messages],
        "reasoning": req.reasoning,
        "mode": req.mode,
        "subject": req.subject,
    }


# ---------- auth ----------

@app.post("/api/v1/auth/register")
async def register(req: RegisterRequest, response: Response) -> UserResponse:
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    name = req.display_name.strip() or email.split("@")[0]
    if db.get_user_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="That email already has an account — try logging in")
    user = db.create_user(email, name, hash_password(req.password))
    set_session_cookie(response, db.create_session(user["id"]))
    return UserResponse(id=user["id"], email=user["email"], displayName=name)


@app.post("/api/v1/auth/login")
async def login(req: LoginRequest, response: Response) -> UserResponse:
    user = db.get_user_by_email(req.email.strip().lower())
    if user is None or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email or password doesn't match")
    set_session_cookie(response, db.create_session(user["id"]))
    return UserResponse(
        id=user["id"], email=user["email"], displayName=user["display_name"]
    )


@app.post("/api/v1/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    db.delete_session(session_token(request))
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/v1/auth/me")
async def me(user=Depends(current_user)) -> UserResponse:
    return UserResponse(
        id=user["id"], email=user["email"], displayName=user["display_name"]
    )


@app.patch("/api/v1/auth/me")
async def update_me(
    req: UpdateProfileRequest, user=Depends(current_user)
) -> UserResponse:
    name = req.display_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Display name can't be empty")
    db.update_user_display_name(user["id"], name)
    return UserResponse(id=user["id"], email=user["email"], displayName=name)


@app.post("/api/v1/auth/change-password")
async def change_password(
    req: ChangePasswordRequest, user=Depends(current_user)
) -> dict:
    if not verify_password(req.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password doesn't match")
    if len(req.new_password) < 8:
        raise HTTPException(
            status_code=400, detail="New password must be at least 8 characters"
        )
    db.update_user_password(user["id"], hash_password(req.new_password))
    return {"ok": True}


# ---------- chats ----------

@app.get("/api/v1/chats")
async def list_chats(user=Depends(current_user)) -> list[dict]:
    return db.list_chats(user["id"])


@app.post("/api/v1/chats")
async def create_chat(req: CreateChatRequest, user=Depends(current_user)) -> dict:
    if req.mode not in ("chat", "tutor"):
        raise HTTPException(status_code=400, detail="Unknown mode")
    return db.create_chat(user["id"], mode=req.mode, subject=req.subject)


@app.patch("/api/v1/chats/{chat_id}")
async def rename_chat(chat_id: str, req: RenameChatRequest, user=Depends(current_user)) -> dict:
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title can't be empty")
    if not db.rename_chat(user["id"], chat_id, title):
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"ok": True}


@app.delete("/api/v1/chats/{chat_id}")
async def delete_chat(chat_id: str, user=Depends(current_user)) -> dict:
    if not db.delete_chat(user["id"], chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"ok": True}


# ---------- chat completion ----------

def _owned_chat_or_404(user, req: ChatRequest):
    chat = db.get_chat(user["id"], req.chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


def _sync_history(chat_id: str, req: ChatRequest) -> None:
    """Full-history sync: stored messages mirror what the client sent.

    Idempotent across retries since the client sends the entire history
    of the chat each time (truncated back to the last user message on retry).
    """
    history = [
        {
            "role": m.role,
            "content": m.content,
            "reasoning": m.reasoning or "",
            "tools": m.tools or [],
            "parts": m.parts or [],
        }
        for m in req.messages
    ]
    db.replace_chat_messages(chat_id, history)
    db.update_chat_meta(
        chat_id,
        mode=req.mode,
        subject=req.subject,
        clear_subject=req.subject is None,
    )


def _serialize_tool_calls(tool_calls: list[ToolCall]) -> list[dict]:
    return json.loads(json.dumps([t.model_dump() for t in tool_calls]))


@app.post("/api/v1/chat")
async def chat(req: ChatRequest, user=Depends(current_user)) -> ChatResponse:
    _owned_chat_or_404(user, req)
    result = await GRAPH.ainvoke(_state(req))
    messages = result["messages"]
    reasoning, tool_calls, parts = _final_extras(messages)
    db.append_chat_message(
        req.chat_id,
        role="assistant",
        content=_text(messages[-1].content),
        reasoning=reasoning,
        tools=_serialize_tool_calls(tool_calls),
        parts=parts,
    )
    return ChatResponse(
        content=_text(messages[-1].content),
        reasoning=reasoning,
        tool_calls=tool_calls,
    )


@app.post("/api/v1/chat/stream")
async def chat_stream(req: ChatRequest, user=Depends(current_user)):
    _owned_chat_or_404(user, req)

    async def events():
        _sync_history(req.chat_id, req)
        yield {"event": "start", "data": "{}"}
        final_text = ""
        reasoning_text = ""
        tools_by_id: dict[str, dict] = {}
        parts: list[dict] = []
        parts_by_tool_id: dict[str, dict] = {}
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
                    reasoning_text += reasoning
                    if parts and parts[-1].get("type") == "reasoning":
                        parts[-1]["text"] += reasoning
                    else:
                        parts.append({"type": "reasoning", "text": reasoning})
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
                tools_by_id[call_id] = {
                    "id": call_id,
                    "name": name,
                    "display": TOOL_DISPLAY_NAMES.get(name, name),
                    "args": args,
                    "result": "",
                    "ok": True,
                }
                part = {
                    "type": "tool",
                    "id": str(call_id),
                    "name": name,
                    "display": TOOL_DISPLAY_NAMES.get(name, name),
                    "args": args,
                    "result": None,
                    "ok": None,
                }
                parts.append(part)
                parts_by_tool_id[str(call_id)] = part
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
                output_text = _tool_result_text(event["data"].get("output", ""))
                if event.get("run_id", "") in tools_by_id:
                    tools_by_id[event["run_id"]]["result"] = output_text
                tool_part = parts_by_tool_id.get(str(event.get("run_id", "")))
                if tool_part is not None:
                    tool_part["result"] = output_text
                    tool_part["ok"] = True
                yield {
                    "event": "tool_result",
                    "data": json.dumps(
                        {
                            "id": str(event.get("run_id", "")),
                            "name": event.get("name", ""),
                            "content": output_text,
                            "ok": True,
                        }
                    ),
                }
            elif kind == "on_tool_error":
                error_text = str(event["data"].get("error", ""))
                if event.get("run_id", "") in tools_by_id:
                    tools_by_id[event["run_id"]]["result"] = error_text
                    tools_by_id[event["run_id"]]["ok"] = False
                tool_part = parts_by_tool_id.get(str(event.get("run_id", "")))
                if tool_part is not None:
                    tool_part["result"] = error_text
                    tool_part["ok"] = False
                yield {
                    "event": "tool_result",
                    "data": json.dumps(
                        {
                            "id": str(event.get("run_id", "")),
                            "name": event.get("name", ""),
                            "content": error_text,
                            "ok": False,
                        }
                    ),
                }
        yield {"event": "done", "data": "{}"}
        if req.generate_title and req.messages:
            title = await generate_title(req.messages[0].content, final_text)
            if title:
                db.set_chat_title_if_default(req.chat_id, title)
                yield {"event": "title", "data": json.dumps({"title": title})}
        if final_text or reasoning_text or tools_by_id:
            db.append_chat_message(
                req.chat_id,
                role="assistant",
                content=final_text,
                reasoning=reasoning_text,
                tools=list(tools_by_id.values()),
                parts=[json.loads(json.dumps(p)) for p in parts],
            )

    return EventSourceResponse(events(), ping=15)
