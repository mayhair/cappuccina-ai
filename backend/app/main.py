import base64
import json
import time
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from sse_starlette.sse import EventSourceResponse

from agent.content import flatten_content
from agent.fmhy import load_fmhy_tools
from agent.graph import build_graph
from agent.title import generate_title
from agent.tools import ALL_TOOLS, TOOL_DISPLAY_NAMES
from app import calendar, db, documents, gmail, google_oauth, tts
from app.auth import (
    clear_session_cookie,
    current_user,
    session_token,
    set_session_cookie,
)
from app.config import (
    APP_ORIGIN,
    FISH_API_KEY,
    GOOGLE_CALENDAR_SCOPES,
    GOOGLE_GMAIL_SCOPES,
    GOOGLE_LOGIN_SCOPES,
)
from app.google_oauth import ServiceNotConnected
from app.schemas import (
    CalendarStatusResponse,
    ChatRequest,
    ChatResponse,
    CreateChatRequest,
    GmailStatusResponse,
    RenameChatRequest,
    TtsJobResponse,
    TtsRequest,
    ToolCall,
    UpdateDraftRequest,
    UpdateProfileRequest,
    UserResponse,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the FMHY free-media tools, then compile the graph around them.

    The endpoint is best-effort: if it is down, the tools are simply absent and
    the rest of the chatbot boots as usual.
    """
    global GRAPH
    fmhy_tools = await load_fmhy_tools()
    GRAPH = build_graph([*ALL_TOOLS, *fmhy_tools])
    yield


GRAPH = build_graph(ALL_TOOLS)

app = FastAPI(title="Cappuccina Chatbot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


def _reasoning_text(content) -> str:
    """Reasoning trace from Responses-API-style content blocks.

    OpenRouter (Chat Completions) reports reasoning separately, on the
    message's `additional_kwargs`, so plain string content is never reasoning.
    """
    if isinstance(content, str):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "reasoning":
            for summary in block.get("summary", []):
                if isinstance(summary, dict) and summary.get("text"):
                    parts.append(summary["text"])
    return "".join(parts)


def _message_reasoning(msg, content) -> str:
    """Reasoning for one model message: OpenRouter's field first, then content."""
    kwargs = getattr(msg, "additional_kwargs", None) or {}
    reasoning = kwargs.get("reasoning") or kwargs.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        return reasoning
    return _reasoning_text(content)


def _tool_label(name: str, args: dict) -> str:
    rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return f"{name}({rendered})"


def _tool_result_text(output) -> str:
    return flatten_content(getattr(output, "content", output))


def _image_part_from_result(name: str, output_text: str) -> dict | None:
    """An {"type": "image"} part for a successful generate_image/image_edit result.

    The part only references the saved image by id â€” the bytes live in
    backend/data/images and are served by GET /api/v1/images/{id}, so they
    never flow through the full-history sync payloads. Returns None for any
    other tool or a failed/malformed result.
    """
    if name not in ("generate_image", "image_edit") or not output_text:
        return None
    try:
        data = json.loads(output_text)
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("status") not in ("image_generated", "image_edited")
        or not data.get("image_id")
    ):
        return None
    part = {
        "type": "image",
        "imageId": data["image_id"],
        "prompt": data.get("prompt", ""),
        "width": data.get("width"),
        "height": data.get("height"),
    }
    # OpenAI-only: marks a PNG with a transparent background so the UI can
    # show a checkerboard behind it.
    if data.get("background") == "transparent":
        part["transparent"] = True
    return part


def _final_extras(messages) -> tuple[str, list[ToolCall], list[dict]]:
    """Reasoning, tool calls, and the ordered interleaving of the two."""
    reasoning = ""
    results_by_id: dict[str, str] = {}
    pending: list[tuple[str, dict, str]] = []
    parts: list[dict] = []
    tools_by_part_id: dict[str, dict] = {}
    for msg in messages:
        content = getattr(msg, "content", "")
        step_reasoning = _message_reasoning(msg, content)
        if step_reasoning:
            reasoning += step_reasoning
            parts.append({"type": "reasoning", "text": step_reasoning})
        # Text the model emitted on this turn. Kept as its own part so the
        # UI can interleave it between thinking blocks and tool calls. Tool
        # results are not model text, so they never become text parts; system
        # messages (including the injected FMHY safety note) never surface.
        if msg.__class__.__name__ not in ("ToolMessage", "SystemMessage"):
            step_text = flatten_content(content)
            if step_text:
                parts.append({"type": "text", "text": step_text})
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
            output_text = _tool_result_text(getattr(msg, "content", ""))
            results_by_id[getattr(msg, "tool_call_id", "") or ""] = output_text
            part = tools_by_part_id.get(getattr(msg, "tool_call_id", "") or "")
            if part is not None:
                part["result"] = output_text
                image_part = _image_part_from_result(part.get("name", ""), output_text)
                if image_part is not None:
                    parts.append(image_part)
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


def _mark_sent_drafts(chats: list[dict], sent_ids: set[str]) -> None:
    """Flag stored draft_email tool parts whose draft has already been sent.

    Done at read time so it survives full-history syncs from the client.
    """
    if not sent_ids:
        return
    for chat in chats:
        for message in chat.get("messages", []):
            for key in ("parts", "tools"):
                for tool in message.get(key) or []:
                    if not isinstance(tool, dict) or tool.get("name") != "draft_email":
                        continue
                    try:
                        data = json.loads(tool.get("result") or "")
                    except (ValueError, TypeError):
                        continue
                    if isinstance(data, dict) and data.get("draft_id") in sent_ids:
                        tool["sent"] = True


def _mark_deleted_events(chats: list[dict], deleted_ids: set[str]) -> None:
    """Flag stored propose_event_deletion tool parts whose event is gone.

    Done at read time so it survives full-history syncs from the client.
    """
    if not deleted_ids:
        return
    for chat in chats:
        for message in chat.get("messages", []):
            for key in ("parts", "tools"):
                for tool in message.get(key) or []:
                    if (
                        not isinstance(tool, dict)
                        or tool.get("name") != "propose_event_deletion"
                    ):
                        continue
                    try:
                        data = json.loads(tool.get("result") or "")
                    except (ValueError, TypeError):
                        continue
                    if isinstance(data, dict) and data.get("event_id") in deleted_ids:
                        tool["deleted"] = True


def _attachment_blocks(parts, user_id: str) -> list[dict]:
    """LLM content blocks for a user turn's attachment parts.

    Images become vision (`image_url`) blocks read from disk. Documents become
    a text pointer naming the file and its id â€” the model calls read_attachment
    to pull the extracted text on demand, so document bodies never sit in the
    prompt or in the stored history.
    """
    blocks: list[dict] = []
    for part in parts or []:
        if not isinstance(part, dict) or part.get("type") != "attachment":
            continue
        attachment_id = part.get("attachmentId")
        if not attachment_id:
            continue
        row = db.get_attachment(user_id, attachment_id)
        if row is None:
            continue
        if row["kind"] == "image":
            path = db.attachment_file_path(row["id"], row["filename"])
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            encoded = base64.b64encode(raw).decode("ascii")
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{row['mime']};base64,{encoded}"},
                }
            )
            blocks.append(
                {
                    "type": "text",
                    "text": f'[Attached image "{row["filename"]}" (id={row['id']})]',
                }
            )
        else:
            blocks.append(
                {
                    "type": "text",
                    "text": f'[Attached file "{row["filename"]}" (id={row['id']})]',
                }
            )
    return blocks


def _assistant_content(m) -> str:
    """Assistant text plus pointers to any pictures it produced.

    Prior turns are replayed as plain text, so an image's id would otherwise be
    invisible to the model and it could not call image_edit on an earlier
    picture (it would redraw from scratch instead). The pointer names the id;
    the bytes stay server-side.
    """
    text = m.content or ""
    pointers = [
        f"[Generated image (id={part.get('imageId')})]"
        for part in m.parts or []
        if isinstance(part, dict)
        and part.get("type") == "image"
        and part.get("imageId")
    ]
    if pointers:
        text = (text + "\n" + "\n".join(pointers)).strip()
    return text


def _valid_timezone(tz: str | None) -> str | None:
    if not tz:
        return None
    try:
        ZoneInfo(tz)
    except Exception:
        return None
    return tz


def _resolve_timezone(req: ChatRequest, user) -> str:
    """The user's IANA timezone: the browser's value when valid, else the
    connected Google Calendar's zone, else UTC."""
    tz = _valid_timezone(req.timezone)
    if tz:
        return tz
    if db.get_google_grant(user["id"], "calendar") is not None:
        try:
            return calendar.get_timezone(user["id"])
        except Exception:
            pass
    return "UTC"


def _state(req: ChatRequest, user) -> dict:
    messages = []
    for m in req.messages:
        if m.role == "assistant":
            messages.append(AIMessage(content=_assistant_content(m)))
            continue
        if m.role != "user":
            messages.append(SystemMessage(content=m.content))
            continue
        blocks = _attachment_blocks(m.parts, user["id"])
        if not blocks:
            messages.append(HumanMessage(content=m.content))
            continue
        content: list[dict] = []
        if m.content:
            content.append({"type": "text", "text": m.content})
        content.extend(blocks)
        messages.append(HumanMessage(content=content))
    return {
        "messages": messages,
        "reasoning": req.reasoning,
        "mode": req.mode,
        "subject": req.subject,
        "timezone": _resolve_timezone(req, user),
    }


# ---------- auth ----------

def _user_response(user) -> UserResponse:
    return UserResponse(
        id=user["id"],
        email=user["email"],
        displayName=user["display_name"],
        avatarUrl=user["avatar_url"] if "avatar_url" in user.keys() else None,
        gmailConnected=db.get_google_grant(user["id"], "gmail") is not None,
        calendarConnected=db.get_google_grant(user["id"], "calendar") is not None,
    )


@app.get("/api/v1/auth/google/start")
async def google_start() -> RedirectResponse:
    """Kick off sign-in: bounce the browser to Google's consent screen."""
    if not google_oauth.is_configured():
        return RedirectResponse(f"{APP_ORIGIN}/?auth_error=google_not_configured")
    state = db.create_oauth_state("login")
    url = google_oauth.build_auth_url(
        state, GOOGLE_LOGIN_SCOPES, google_oauth.login_redirect_uri()
    )
    return RedirectResponse(url)


@app.get("/api/v1/auth/google/callback")
async def google_callback(request: Request) -> RedirectResponse:
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"{APP_ORIGIN}/?auth_error={error}")
    if not db.consume_oauth_state(request.query_params.get("state", ""), "login"):
        return RedirectResponse(f"{APP_ORIGIN}/?auth_error=invalid_state")
    code = request.query_params.get("code", "")
    if not code:
        return RedirectResponse(f"{APP_ORIGIN}/?auth_error=missing_code")

    try:
        tokens = google_oauth.exchange_code(code, google_oauth.login_redirect_uri())
        info = google_oauth.fetch_userinfo(tokens.get("access_token", ""))
    except Exception:
        return RedirectResponse(f"{APP_ORIGIN}/?auth_error=exchange_failed")

    sub = info.get("sub", "")
    email = (info.get("email") or "").strip().lower()
    if not sub or not email or not info.get("email_verified", False):
        return RedirectResponse(f"{APP_ORIGIN}/?auth_error=email_unverified")

    user = db.get_user_by_google_sub(sub)
    if user is None:
        existing = db.get_user_by_email(email)
        if existing is not None:
            # Legacy email/password account: link this Google identity to it.
            db.link_google_account(existing["id"], sub, info.get("picture"))
            user = db.get_user_by_id(existing["id"])
        else:
            name = (info.get("name") or email.split("@")[0]).strip() or "friend"
            user = db.create_google_user(email, name, sub, info.get("picture"))

    if user is None:
        return RedirectResponse(f"{APP_ORIGIN}/?auth_error=account_error")

    response = RedirectResponse(f"{APP_ORIGIN}/")
    set_session_cookie(response, db.create_session(user["id"]))
    return response


@app.post("/api/v1/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    db.delete_session(session_token(request))
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/v1/auth/me")
async def me(user=Depends(current_user)) -> UserResponse:
    return _user_response(user)


@app.patch("/api/v1/auth/me")
async def update_me(
    req: UpdateProfileRequest, user=Depends(current_user)
) -> UserResponse:
    name = req.display_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Display name can't be empty")
    db.update_user_display_name(user["id"], name)
    return _user_response(db.get_user_by_id(user["id"]))


# ---------- Gmail (incremental consent) ----------

@app.get("/api/v1/gmail/connect")
async def gmail_connect(user=Depends(current_user)) -> RedirectResponse:
    if not google_oauth.is_configured():
        return RedirectResponse(f"{APP_ORIGIN}/?gmail=error")
    state = db.create_oauth_state("gmail", user["id"])
    url = google_oauth.build_auth_url(
        state,
        GOOGLE_GMAIL_SCOPES,
        google_oauth.gmail_redirect_uri(),
        prompt="consent",
        offline=True,
    )
    return RedirectResponse(url)


@app.get("/api/v1/gmail/callback")
async def gmail_callback(request: Request, user=Depends(current_user)) -> RedirectResponse:
    if request.query_params.get("error"):
        return RedirectResponse(f"{APP_ORIGIN}/?gmail=error")
    state = db.consume_oauth_state(request.query_params.get("state", ""), "gmail")
    if state is None or state["user_id"] != user["id"]:
        return RedirectResponse(f"{APP_ORIGIN}/?gmail=error")
    code = request.query_params.get("code", "")
    if not code:
        return RedirectResponse(f"{APP_ORIGIN}/?gmail=error")
    try:
        tokens = google_oauth.exchange_code(code, google_oauth.gmail_redirect_uri())
        google_oauth.store_grant(user["id"], "gmail", tokens)
    except Exception:
        return RedirectResponse(f"{APP_ORIGIN}/?gmail=error")
    return RedirectResponse(f"{APP_ORIGIN}/?gmail=connected")


@app.get("/api/v1/gmail/status")
async def gmail_status(user=Depends(current_user)) -> GmailStatusResponse:
    grant = db.get_google_grant(user["id"], "gmail")
    if grant is None:
        return GmailStatusResponse(connected=False)
    return GmailStatusResponse(connected=True, scopes=grant["scopes"].split())


@app.post("/api/v1/gmail/disconnect")
async def gmail_disconnect(user=Depends(current_user)) -> dict:
    db.delete_google_grant(user["id"], "gmail")
    return {"ok": True}


@app.patch("/api/v1/gmail/drafts/{draft_id}")
async def gmail_update_draft(
    draft_id: str, req: UpdateDraftRequest, user=Depends(current_user)
) -> dict:
    """Let the user edit a draft (add their name, tweak wording) before sending."""
    if not req.to.strip():
        raise HTTPException(status_code=400, detail="Add a recipient first")
    try:
        updated = gmail.update_draft(
            user["id"], draft_id, req.to.strip(), req.subject, req.body
        )
    except ServiceNotConnected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except gmail.DraftGone:
        raise HTTPException(
            status_code=409,
            detail="That draft no longer exists â€” it may already have been sent",
        )
    except gmail.GmailApiError as exc:
        raise HTTPException(status_code=502, detail=f"Gmail: {exc}")
    return {"ok": True, **updated}


@app.post("/api/v1/gmail/drafts/{draft_id}/send")
async def gmail_send_draft(draft_id: str, user=Depends(current_user)) -> dict:
    """The only path that actually sends mail â€” driven by the user's button."""
    try:
        sent = gmail.send_draft(user["id"], draft_id)
    except ServiceNotConnected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except gmail.DraftGone:
        raise HTTPException(
            status_code=409,
            detail="That draft no longer exists â€” it may already have been sent",
        )
    except gmail.GmailApiError as exc:
        raise HTTPException(status_code=502, detail=f"Gmail: {exc}")
    db.mark_draft_sent(user["id"], draft_id, sent.get("message_id", ""))
    return {"ok": True, **sent}


# ---------- Calendar (incremental consent) ----------

@app.get("/api/v1/calendar/connect")
async def calendar_connect(user=Depends(current_user)) -> RedirectResponse:
    if not google_oauth.is_configured():
        return RedirectResponse(f"{APP_ORIGIN}/?calendar=error")
    state = db.create_oauth_state("calendar", user["id"])
    url = google_oauth.build_auth_url(
        state,
        GOOGLE_CALENDAR_SCOPES,
        google_oauth.calendar_redirect_uri(),
        prompt="consent",
        offline=True,
    )
    return RedirectResponse(url)


@app.get("/api/v1/calendar/callback")
async def calendar_callback(
    request: Request, user=Depends(current_user)
) -> RedirectResponse:
    if request.query_params.get("error"):
        return RedirectResponse(f"{APP_ORIGIN}/?calendar=error")
    state = db.consume_oauth_state(request.query_params.get("state", ""), "calendar")
    if state is None or state["user_id"] != user["id"]:
        return RedirectResponse(f"{APP_ORIGIN}/?calendar=error")
    code = request.query_params.get("code", "")
    if not code:
        return RedirectResponse(f"{APP_ORIGIN}/?calendar=error")
    try:
        tokens = google_oauth.exchange_code(code, google_oauth.calendar_redirect_uri())
        google_oauth.store_grant(user["id"], "calendar", tokens)
    except Exception:
        return RedirectResponse(f"{APP_ORIGIN}/?calendar=error")
    return RedirectResponse(f"{APP_ORIGIN}/?calendar=connected")


@app.get("/api/v1/calendar/status")
async def calendar_status(user=Depends(current_user)) -> CalendarStatusResponse:
    grant = db.get_google_grant(user["id"], "calendar")
    if grant is None:
        return CalendarStatusResponse(connected=False)
    return CalendarStatusResponse(connected=True, scopes=grant["scopes"].split())


@app.post("/api/v1/calendar/disconnect")
async def calendar_disconnect(user=Depends(current_user)) -> dict:
    db.delete_google_grant(user["id"], "calendar")
    return {"ok": True}


@app.post("/api/v1/calendar/events/{event_id}/delete")
async def calendar_delete_event(event_id: str, user=Depends(current_user)) -> dict:
    """The only path that deletes an event â€” driven by the user's button.

    The assistant proposes the deletion in chat; nothing is removed until the
    user confirms here.
    """
    try:
        calendar.delete_event(user["id"], event_id)
    except ServiceNotConnected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except calendar.EventGone:
        raise HTTPException(
            status_code=409,
            detail="That event is already gone â€” it may have been deleted already",
        )
    except calendar.CalendarApiError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar: {exc}")
    db.mark_event_deleted(user["id"], event_id)
    return {"ok": True, "event_id": event_id}


# ---------- chats ----------

@app.get("/api/v1/chats")
async def list_chats(user=Depends(current_user)) -> list[dict]:
    chats = db.list_chats(user["id"])
    _mark_sent_drafts(chats, db.get_sent_draft_ids(user["id"]))
    _mark_deleted_events(chats, db.get_deleted_event_ids(user["id"]))
    return chats


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


@app.get("/api/v1/images/{image_id}")
async def get_image(image_id: str, user=Depends(current_user)):
    """Serve one generated image. Authorized per user (unlike a static mount)
    so private chat images stay private; the session cookie is sent
    automatically by same-origin <img> tags. Images are immutable (a new
    generation gets a new id), so alongside FileResponse's ETag / Last-Modified
    we send an immutable Cache-Control. ``private`` keeps shared caches (for
    example Cloudflare) from storing it; it is a browser fallback only."""
    row = db.get_image(user["id"], image_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Image not found")
    path = db.image_file_path(row["id"], row["mime"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(
        path,
        media_type=row["mime"],
        headers={"Cache-Control": "private, max-age=5184000, immutable"},
    )


# ---------- attachments (user uploads) ----------

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def _attachment_response(row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "filename": row["filename"],
        "mime": row["mime"],
        "size": row["size_bytes"],
        "charCount": row["char_count"],
    }


@app.post("/api/v1/attachments")
async def upload_attachment(
    file: UploadFile = File(...),
    chat_id: str = Form(..., alias="chatId"),
    user=Depends(current_user),
) -> dict:
    """Store one uploaded file. Images keep their bytes; documents are parsed
    to text here so read_attachment can serve it without re-parsing."""
    if db.get_chat(user["id"], chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    filename = (file.filename or "file").strip() or "file"
    data = await file.read()

    if documents.is_image(filename):
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="That image is too large (20MB max)")
        mime = documents.mime_for(filename)
        # Oversized pictures are shrunk before storage so they stay cheap to
        # serve and to send to the model.
        data = documents.downscale_image(data, mime)
        kind, text = "image", ""
    elif documents.is_document(filename):
        if len(data) > MAX_DOCUMENT_BYTES:
            raise HTTPException(status_code=413, detail="That file is too large (10MB max)")
        try:
            text = documents.extract_text(filename, data)
        except documents.UnsupportedDocument as exc:
            raise HTTPException(status_code=415, detail=str(exc))
        kind, mime = "document", documents.mime_for(filename)
    else:
        raise HTTPException(
            status_code=415,
            detail="That file type isn't supported. Try an image, PDF, Word, "
            "PowerPoint, Excel, or a text file.",
        )
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty")

    row = db.create_attachment(
        user["id"], chat_id, filename, data, kind=kind, mime=mime, text=text
    )
    return _attachment_response(row)


@app.get("/api/v1/attachments/{attachment_id}")
async def get_attachment_file(attachment_id: str, user=Depends(current_user)):
    """Serve one uploaded file, user-scoped. Images render inline (used as the
    preview source); documents download under their original name."""
    row = db.get_attachment(user["id"], attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = db.attachment_file_path(row["id"], row["filename"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    inline = row["kind"] == "image"
    return FileResponse(
        path,
        media_type=row["mime"],
        filename=None if inline else row["filename"],
        content_disposition_type="inline" if inline else "attachment",
    )


@app.delete("/api/v1/attachments/{attachment_id}")
async def delete_attachment_file(attachment_id: str, user=Depends(current_user)) -> dict:
    if not db.delete_attachment(user["id"], attachment_id):
        raise HTTPException(status_code=404, detail="Attachment not found")
    return {"ok": True}


# ---------- chat completion ----------

def _owned_chat_or_404(user, req: ChatRequest):
    chat = db.get_chat(user["id"], req.chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


def _graph_config(user, req: ChatRequest) -> RunnableConfig:
    """Invisible tool context: user id (auth scoping) and chat id (so
    generate_image/image_edit can persist pictures against the current chat)."""
    return {"configurable": {"user_id": user["id"], "chat_id": req.chat_id}}


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
            "thinking_done": m.thinking_done,
            "thought_seconds": m.thought_seconds,
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
    think_start = time.monotonic()
    result = await GRAPH.ainvoke(_state(req, user), config=_graph_config(user, req))
    thought_seconds = time.monotonic() - think_start
    messages = result["messages"]
    reasoning, tool_calls, parts = _final_extras(messages)
    db.append_chat_message(
        req.chat_id,
        role="assistant",
        content=flatten_content(messages[-1].content),
        reasoning=reasoning,
        tools=_serialize_tool_calls(tool_calls),
        parts=parts,
        thinking_done=True,
        thought_seconds=thought_seconds,
    )
    return ChatResponse(
        content=flatten_content(messages[-1].content),
        reasoning=reasoning,
        tool_calls=tool_calls,
    )


class _ThinkingBlockTimer:
    """Stamps each run of consecutive reasoning/tool parts with its duration.

    A "block" opens when the first reasoning or tool part of a run arrives and
    closes when text follows (or the stream ends), at which point every part in
    the run is stamped with that run's duration. This keeps the counter running
    while the model reasons after a tool call instead of stopping at the first
    bit of text.
    """

    def __init__(self, parts: list[dict]):
        self._parts = parts
        self._start: float | None = None
        self._first_index: int | None = None

    def open(self) -> None:
        if self._start is None:
            self._start = time.monotonic()
            self._first_index = len(self._parts)

    def close(self) -> None:
        if self._start is None or self._first_index is None:
            return
        seconds = round(time.monotonic() - self._start, 1)
        for part in self._parts[self._first_index :]:
            if part.get("type") in ("reasoning", "tool"):
                part["seconds"] = seconds
        self._start = None
        self._first_index = None


async def _stream_chat_events(req: ChatRequest, user: dict):
    """SSE events for one chat completion; stores the reply when the run ends."""
    _sync_history(req.chat_id, req)
    yield {"event": "start", "data": "{}"}
    think_start = time.monotonic()
    final_text = ""
    reasoning_text = ""
    tools_by_id: dict[str, dict] = {}
    parts: list[dict] = []
    parts_by_tool_id: dict[str, dict] = {}
    # Every tool run we have seen, so a tool that calls another tool (an
    # internal, nested run) is not mistaken for a second model tool call.
    tool_run_ids: set[str] = set()
    blocks = _ThinkingBlockTimer(parts)

    async for event in GRAPH.astream_events(
        _state(req, user),
        config=_graph_config(user, req),
        version="v2",
    ):
        kind = event["event"]
        if kind == "on_chat_model_start":
            final_text = ""
        elif kind == "on_chat_model_stream":
            chunk = event["data"]["chunk"]
            content = getattr(chunk, "content", "")
            text = flatten_content(content)
            if text:
                final_text += text
                # Text ends the current thinking block: stamp its duration.
                blocks.close()
                # A text part is appended (or extended) so the stored parts
                # preserve the model's text/reasoning/tool interleaving.
                if parts and parts[-1].get("type") == "text":
                    parts[-1]["text"] += text
                else:
                    parts.append({"type": "text", "text": text})
                yield {"event": "token", "data": json.dumps({"content": text})}
            reasoning = _message_reasoning(chunk, content)
            if reasoning:
                reasoning_text += reasoning
                blocks.open()
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
            # A run whose parent is another tool run is the tool calling
            # itself internally â€” surface it once, as the outer call.
            nested = bool(set(event.get("parent_ids") or ()) & tool_run_ids)
            tool_run_ids.add(call_id)
            if nested:
                continue
            args = event["data"].get("input", {}) or {}
            if not isinstance(args, dict):
                args = {"input": args}
            name = event.get("name", "")
            blocks.open()
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
            call_id = event.get("run_id", "")
            if call_id not in tools_by_id:
                # A nested internal run we deliberately did not surface.
                continue
            output_text = _tool_result_text(event["data"].get("output", ""))
            tools_by_id[call_id]["result"] = output_text
            tool_part = parts_by_tool_id.get(str(call_id))
            if tool_part is not None:
                tool_part["result"] = output_text
                tool_part["ok"] = True
            yield {
                "event": "tool_result",
                "data": json.dumps(
                    {
                        "id": str(call_id),
                        "name": event.get("name", ""),
                        "content": output_text,
                        "ok": True,
                    }
                ),
            }
            image_part = _image_part_from_result(
                event.get("name", ""), output_text
            )
            if image_part is not None:
                parts.append(image_part)
                yield {"event": "image", "data": json.dumps(image_part)}
        elif kind == "on_tool_error":
            call_id = event.get("run_id", "")
            if call_id not in tools_by_id:
                continue
            error_text = str(event["data"].get("error", ""))
            tools_by_id[call_id]["result"] = error_text
            tools_by_id[call_id]["ok"] = False
            tool_part = parts_by_tool_id.get(str(call_id))
            if tool_part is not None:
                tool_part["result"] = error_text
                tool_part["ok"] = False
            yield {
                "event": "tool_result",
                "data": json.dumps(
                    {
                        "id": str(call_id),
                        "name": event.get("name", ""),
                        "content": error_text,
                        "ok": False,
                    }
                ),
            }
    yield {"event": "done", "data": "{}"}
    blocks.close()
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
            thinking_done=True,
            thought_seconds=time.monotonic() - think_start,
        )


@app.post("/api/v1/chat/stream")
async def chat_stream(req: ChatRequest, user=Depends(current_user)):
    _owned_chat_or_404(user, req)
    return EventSourceResponse(_stream_chat_events(req, user), ping=15)


# ---------- text-to-speech (read replies aloud) ----------


@app.post("/api/v1/tts")
async def start_tts(req: TtsRequest, user=Depends(current_user)) -> TtsJobResponse:
    """Register the text for speech synthesis. Returns a job id whose audio
    /api/v1/tts/{id} streams as audio/mpeg (fetchable once)."""
    if not FISH_API_KEY:
        raise HTTPException(status_code=503, detail="Text-to-speech is not configured")
    return TtsJobResponse(id=tts.create_job(user["id"], req.text))


@app.get("/api/v1/tts/{job_id}")
async def stream_tts(job_id: str, user=Depends(current_user)):
    """Stream the synthesized MP3. The <audio> element starts playing as soon
    as the first bytes arrive; Fish Audio may still be generating the rest."""
    text = tts.pop_job(job_id, user["id"])
    if text is None:
        raise HTTPException(status_code=404, detail="Audio not found")
    try:
        client, response = await tts.open_audio_stream(text)
    except tts.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return StreamingResponse(
        tts.audio_chunks(client, response),
        media_type="audio/mpeg",
    )
