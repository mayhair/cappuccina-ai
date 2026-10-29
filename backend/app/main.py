import asyncio
import base64
import io
import json
import re
import threading
import time
import zipfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from sse_starlette.sse import EventSourceResponse

from agent.content import flatten_content
from agent.fmhy import load_fmhy_tools
from agent.graph import build_graph, is_viewed_image_message, viewed_images_message_for
from agent import memory as memory_rules
from agent.title import generate_title
from agent.tools import (
    ALL_TOOLS,
    TOOL_DISPLAY_NAMES,
    VIEW_IMAGE_TOOL,
    _webpage_path,
    validate_webpage_write,
)
from app import calendar, config, db, documents, gmail, google_oauth, sitebox, tts, webpage_git
from app.auth import (
    clear_session_cookie,
    current_user,
    session_token,
    set_session_cookie,
)
from app.config import (
    ADULT_MODE_ENABLED,
    APP_ORIGIN,
    CALENDAR_ENABLED,
    FISH_API_KEY,
    GMAIL_ENABLED,
    GOOGLE_CALENDAR_SCOPES,
    GOOGLE_GMAIL_SCOPES,
    GOOGLE_LOGIN_SCOPES,
    MEMORY_INJECT_LIMIT,
    MEMORY_MAX_CHARS,
    MEMORY_MAX_PER_USER,
)
from app.google_oauth import ServiceNotConnected
from app.ratelimit import rate_limited_user
from app.webpage_preview import (
    PREVIEW_HEADERS,
    blocked_segment,
    preview_base,
    resolve_preview,
    resolve_under,
    serve_root,
    webpage_mime,
)
from app.schemas import (
    CalendarStatusResponse,
    ChatRequest,
    ChatResponse,
    CreateChatRequest,
    GmailStatusResponse,
    MemoryRequest,
    RenameChatRequest,
    SaveWebpageFileRequest,
    SetActiveBranchRequest,
    SetWebpageVersionRequest,
    TtsJobResponse,
    TtsRequest,
    ToolCall,
    UpdateDraftRequest,
    UpdateProfileRequest,
    UserResponse,
)


async def _stop_idle_sites() -> None:
    """Stop containers that have not been used for a while (see sitebox.stop_idle).

    A stopped container is transparently restarted by `ensure_running`, so this
    is purely about not leaving a bash/npm session running per chat forever.
    """
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(sitebox.stop_idle)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Drop grants for switched-off Google integrations, load the FMHY
    free-media tools, then compile the graph around them.

    The FMHY endpoint is best-effort: if it is down, the tools are simply absent and
    the rest of the chatbot boots as usual.
    """
    global GRAPH
    # A switched-off integration keeps no tokens: delete (and revoke) them.
    off = [s for s, on in (("gmail", GMAIL_ENABLED), ("calendar", CALENDAR_ENABLED)) if not on]
    await asyncio.to_thread(google_oauth.purge_grants, off)
    fmhy_tools = await load_fmhy_tools()
    GRAPH = build_graph([*ALL_TOOLS, *fmhy_tools])
    idle_task = asyncio.create_task(_stop_idle_sites())
    try:
        yield
    finally:
        idle_task.cancel()
        # Drain it so shutdown never leaves a canceled task complaining.
        await asyncio.gather(idle_task, return_exceptions=True)
        await asyncio.to_thread(db.close_pool)


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


def _tool_call_event(tool_id: str, name: str, args: dict) -> dict:
    """The SSE payload for one tool call (early or at execution start)."""
    return {
        "event": "tool_call",
        "data": json.dumps(
            {
                "id": tool_id,
                "name": name,
                "display": TOOL_DISPLAY_NAMES.get(name, name),
                "args": args,
                "label": _tool_label(name, args),
            }
        ),
    }


def _tool_result_event(tool_id: str, name: str, content: str, ok: bool) -> dict:
    """The SSE payload for one tool result."""
    return {
        "event": "tool_result",
        "data": json.dumps(
            {"id": tool_id, "name": name, "content": content, "ok": ok}
        ),
    }


def _claim_streamed_call(streamed_calls: dict, name: str) -> str | None:
    """The UI id of the streamed model tool call that is now executing.

    The model streams a tool call's name and arguments before the graph runs it,
    so the card is shown early (see _stream_chat_events). When the run starts we
    claim the matching streamed call so the same card carries the result. Falls
    back to None for tools the model never streamed (e.g. a nested internal run).
    """
    for info in streamed_calls.values():
        if not info.get("bound") and info.get("name") == name and info.get("ui_id"):
            info["bound"] = True
            return info["ui_id"]
    return None


def _tool_result_text(output) -> str:
    return flatten_content(getattr(output, "content", output))


def _image_part_from_result(name: str, output_text: str) -> dict | None:
    """An {"type": "image"} part for a successful generate_image/image_edit result.

    The part only references the saved image by id — the bytes live in
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


# The tool that writes into a chat's webpage environment (the canvas).
WEBPAGE_FILE_TOOL = "write_webpage_file"


def _webpage_tool_args(name: str, args: dict) -> dict:
    """Stored/streamed args for a webpage write: the file text stays server-side.

    Message parts ride along with every full-history sync, so file content must
    not travel in them (mirrors how image bytes only travel as a small
    {"type": "image", "imageId"} part). Keep the path and size for display.
    """
    if name != WEBPAGE_FILE_TOOL or not isinstance(args, dict):
        return args
    redacted = {"path": args.get("path", "")}
    content = args.get("content")
    if isinstance(content, str):
        redacted["bytes"] = len(content.encode("utf-8"))
    return redacted


def _webpage_result(output_text: str) -> tuple[dict | None, str | None]:
    """One parse of a webpage tool result: its {"type": "webpage"} part.

    The part only references the workspace by id and names its files, so the
    page text never flows through the full-history sync payloads. An asset (a
    picture copied into the folder) or a shell command has no file text of its
    own to show, so the part only marks the workspace as touched; a write keeps
    its path for the card. The iteration id now comes from the run's finalize
    step (one commit per reply), never from a tool result, so the second tuple
    element is always None — the signature stays for its two callers.
    (None, None) for any other tool or a failed/malformed result.
    """
    if not output_text:
        return None, None
    try:
        data = json.loads(output_text)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(data, dict) or not data.get("webpage_id"):
        return None, None
    status = data.get("status")
    if status not in ("webpage_file_written", "webpage_asset_added", "webpage_command_ran"):
        return None, None
    part = {
        "type": "webpage",
        "webpageId": data["webpage_id"],
        "files": data.get("files") or [],
    }
    if status == "webpage_file_written" and data.get("path"):
        part["path"] = data["path"]
    return part, None


def _webpage_part_from_result(output_text: str) -> dict | None:
    """Just the part (the non-streaming path has no iteration to announce)."""
    return _webpage_result(output_text)[0]


def _merge_webpage_part(parts: list[dict], part: dict) -> None:
    """Fold a webpage write into the turn's existing part so one page is one card.

    A page is usually several writes (index.html, styles.css, script.js); the UI
    shows a single card listing the environment's files. The part keeps the
    position of the turn's first write.
    """
    for existing in reversed(parts):
        if (
            isinstance(existing, dict)
            and existing.get("type") == "webpage"
            and existing.get("webpageId") == part["webpageId"]
        ):
            existing["files"] = part["files"]
            if part.get("path"):
                existing["path"] = part["path"]
            return
    parts.append(part)


def _redact_webpage_parts(items: list) -> list:
    """Strip write_webpage_file content from stored parts/tools (defense in depth).

    The server always builds redacted args, but a client can send its own tree
    during full-history sync; this keeps file text out of the database either way.
    """
    out = []
    for item in items or []:
        if (
            isinstance(item, dict)
            and item.get("name") == WEBPAGE_FILE_TOOL
            and isinstance(item.get("args"), dict)
        ):
            item = {**item, "args": _webpage_tool_args(WEBPAGE_FILE_TOOL, item["args"])}
        out.append(item)
    return out


def _final_extras(messages) -> tuple[str, list[ToolCall], list[dict]]:
    """Reasoning, tool calls, and the ordered interleaving of the two."""
    reasoning = ""
    results_by_id: dict[str, str] = {}
    pending: list[tuple[str, dict, str]] = []
    parts: list[dict] = []
    tools_by_part_id: dict[str, dict] = {}
    for msg in messages:
        # Pictures view_image put in front of the model are not part of the
        # reply; the tool card already records the call.
        if is_viewed_image_message(msg):
            continue
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
            args = _webpage_tool_args(name, call.get("args", {}) or {})
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
                webpage_part = _webpage_part_from_result(output_text)
                if webpage_part is not None:
                    _merge_webpage_part(parts, webpage_part)
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


def _flag_tool_results(
    chats: list[dict], tool_name: str, id_key: str, ids: set[str], flag: str
) -> None:
    """Set ``flag`` on stored ``tool_name`` parts whose result's ``id_key`` is in ``ids``.

    Done at read time so it survives full-history syncs from the client: a
    sent draft or a deleted event is recorded server-side, not in the message.
    """
    if not ids:
        return
    for chat in chats:
        for message in chat.get("messages", []):
            for key in ("parts", "tools"):
                for tool in message.get(key) or []:
                    if not isinstance(tool, dict) or tool.get("name") != tool_name:
                        continue
                    try:
                        data = json.loads(tool.get("result") or "")
                    except (ValueError, TypeError):
                        continue
                    if isinstance(data, dict) and data.get(id_key) in ids:
                        tool[flag] = True


# Uploaded and generated pictures never change once written (each id names
# one file), so their data URLs are kept between turns instead of re-reading
# and re-encoding every picture in the history on every send. The rows are
# still looked up per turn, so ownership and deletion are checked as before.
_DATA_URL_CACHE_LIMIT = 64 * 1024 * 1024  # characters, about one byte each
_data_urls: OrderedDict[Path, str] = OrderedDict()
_data_urls_size = 0
_data_urls_lock = threading.Lock()


def _image_data_url(path: Path, mime: str) -> str | None:
    """A ``data:`` URL for a stored picture, or None when its file is gone."""
    global _data_urls_size
    with _data_urls_lock:
        url = _data_urls.get(path)
        if url is not None:
            _data_urls.move_to_end(path)
            return url
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    with _data_urls_lock:
        if path not in _data_urls:
            _data_urls[path] = url
            _data_urls_size += len(url)
        while _data_urls_size > _DATA_URL_CACHE_LIMIT and len(_data_urls) > 1:
            _, dropped = _data_urls.popitem(last=False)
            _data_urls_size -= len(dropped)
    return url


def _attachment_blocks(parts, user_id: str) -> list[dict]:
    """LLM content blocks for a user turn's attachment parts.

    Images become vision (`image_url`) blocks read from disk. Documents become
    a text pointer naming the file and its id — the model calls read_attachment
    to pull the extracted text on demand, so document bodies never sit in the
    prompt or in the stored history. Assets (audio, video, fonts) become a
    pointer saying they are binary and can be placed on a webpage with
    add_webpage_asset.
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
            url = _image_data_url(
                db.attachment_file_path(row["id"], row["filename"]), row["mime"]
            )
            if url is None:
                continue
            blocks.append({"type": "image_url", "image_url": {"url": url}})
            blocks.append(
                {
                    "type": "text",
                    "text": f'[Attached image "{row["filename"]}" (id={row['id']})]',
                }
            )
        elif row["kind"] == "asset":
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        f'[Attached file "{row["filename"]}" (id={row["id"]}, '
                        f'{row["mime"]}, {documents.human_size(row["size_bytes"])}) '
                        "- binary file with no readable text; to use it on a webpage "
                        f'call add_webpage_asset with attachment_id={row["id"]}]'
                    ),
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


def _viewed_image_message(parts, user_id: str) -> HumanMessage | None:
    """The pictures an earlier reply looked at with view_image, shown again.

    A viewed picture stays in context for the rest of the chat (like an
    attached image), so the user can ask follow-up questions about it without
    the model calling view_image again. The saved tool part names the image id;
    the bytes are re-read from disk, user-scoped, and a picture that is gone is
    skipped.
    """
    seen: set[str] = set()
    viewed = []
    for part in parts or []:
        if (
            not isinstance(part, dict)
            or part.get("type") != "tool"
            or part.get("name") != VIEW_IMAGE_TOOL
            or part.get("ok") is False
        ):
            continue
        image_id = (part.get("args") or {}).get("image_id")
        if not isinstance(image_id, str) or not image_id or image_id in seen:
            continue
        seen.add(image_id)
        row = db.get_image(user_id, image_id)
        if row is None:
            continue
        url = _image_data_url(db.image_file_path(row["id"], row["mime"]), row["mime"])
        if url is None:
            continue
        viewed.append((row["id"], url))
    return viewed_images_message_for(viewed)


def _assistant_content(m) -> str:
    """Assistant text plus pointers to any pictures and webpages it produced.

    Prior turns are replayed as plain text, so an image's id (or a webpage's
    file list) would otherwise be invisible to the model — it could not call
    image_edit on an earlier picture or read_webpage_file on an earlier page (it
    would redraw both from scratch instead). The pointers name ids/files; the
    actual bytes and text stay server-side.
    """
    text = m.content or ""
    pointers = [
        f"[Generated image (id={part.get('imageId')})]"
        for part in m.parts or []
        if isinstance(part, dict)
        and part.get("type") == "image"
        and part.get("imageId")
    ]
    pointers += [
        "[Webpage (files: " + ", ".join(part.get("files") or []) + ")]"
        for part in m.parts or []
        if isinstance(part, dict) and part.get("type") == "webpage" and part.get("files")
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


def _gmail_linked(user_id: str) -> bool:
    return GMAIL_ENABLED and db.get_google_grant(user_id, "gmail") is not None


def _calendar_linked(user_id: str) -> bool:
    return CALENDAR_ENABLED and db.get_google_grant(user_id, "calendar") is not None


def _resolve_timezone(req: ChatRequest, user) -> str:
    """The user's IANA timezone: the browser's value when valid, else the
    connected Google Calendar's zone, else UTC."""
    tz = _valid_timezone(req.timezone)
    if tz:
        return tz
    if _calendar_linked(user["id"]):
        try:
            return calendar.get_timezone(user["id"])
        except Exception:
            pass
    return "UTC"


def _state(req: ChatRequest, user) -> dict:
    messages = []
    for m in req.messages:
        if m.role == "assistant":
            # Pictures this reply viewed go first, as they did during the run:
            # the reply's text was written after looking at them.
            viewed = _viewed_image_message(m.parts, user["id"])
            if viewed is not None:
                messages.append(viewed)
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
    memory = bool(user["memory_enabled"])
    return {
        "messages": messages,
        "reasoning": req.reasoning,
        "mode": req.mode,
        "subject": req.subject,
        "timezone": _resolve_timezone(req, user),
        "adult": ADULT_MODE_ENABLED and bool(user["adult_mode"]),
        "gmail": _gmail_linked(user["id"]),
        "calendar": _calendar_linked(user["id"]),
        # Off on the server: no "connect it in Settings" hint in the prompt.
        "gmail_enabled": GMAIL_ENABLED,
        "calendar_enabled": CALENDAR_ENABLED,
        "memory": memory,
        # The newest few ride along in the prompt; search_memories reaches the rest.
        "memories": (
            [dict(row) for row in db.list_memories(user["id"], MEMORY_INJECT_LIMIT)]
            if memory
            else []
        ),
        "memory_total": db.count_memories(user["id"]) if memory else 0,
    }


# ---------- auth ----------

def _user_response(user) -> UserResponse:
    return UserResponse(
        id=user["id"],
        email=user["email"],
        displayName=user["display_name"],
        avatarUrl=user["avatar_url"] if "avatar_url" in user.keys() else None,
        gmailConnected=_gmail_linked(user["id"]),
        calendarConnected=_calendar_linked(user["id"]),
        gmailEnabled=GMAIL_ENABLED,
        calendarEnabled=CALENDAR_ENABLED,
        adultMode=ADULT_MODE_ENABLED and bool(user["adult_mode"]),
        adultModeEnabled=ADULT_MODE_ENABLED,
        memoryEnabled=bool(user["memory_enabled"]),
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
    if req.display_name is not None:
        name = req.display_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Display name can't be empty")
        db.update_user_display_name(user["id"], name)
    if req.adult_mode is not None and ADULT_MODE_ENABLED:
        db.update_user_adult_mode(user["id"], req.adult_mode)
    if req.memory_enabled is not None:
        db.set_memory_enabled(user["id"], req.memory_enabled)
    return _user_response(db.get_user_by_id(user["id"]))


@app.delete("/api/v1/auth/me")
async def delete_me(response: Response, user=Depends(current_user)) -> dict:
    """Delete the account and everything linked to it, then sign out.

    Google tokens are revoked first, while they are still stored. Both steps
    run in worker threads: revoking calls Google, and removing webpage
    containers shells out to the container CLI.
    """
    await asyncio.to_thread(google_oauth.revoke_grants, user["id"])
    await asyncio.to_thread(db.delete_user, user["id"])
    clear_session_cookie(response)
    return {"ok": True}


# ---------- memories (the Settings > Memory manager) ----------
# The agent's memory tools write the same rows; both go through
# memory_rules.validate, so the content rules are identical either way.

def _memory_json(row) -> dict:
    return {
        "id": row["id"],
        "content": row["content"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


@app.get("/api/v1/memories")
async def list_user_memories(user=Depends(current_user)) -> dict:
    return {
        "memories": [_memory_json(row) for row in db.list_memories(user["id"])],
        "enabled": bool(user["memory_enabled"]),
        "limit": MEMORY_MAX_PER_USER,
        "maxChars": MEMORY_MAX_CHARS,
    }


@app.post("/api/v1/memories", status_code=201)
async def create_user_memory(req: MemoryRequest, user=Depends(current_user)) -> dict:
    text, problem = memory_rules.validate(req.content, memory_rules.is_adult(user))
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if db.count_memories(user["id"]) >= MEMORY_MAX_PER_USER:
        raise HTTPException(
            status_code=409,
            detail=f"Memory is full ({MEMORY_MAX_PER_USER} saved). Delete one to make room.",
        )
    return _memory_json(db.create_memory(user["id"], text))


@app.patch("/api/v1/memories/{memory_id}")
async def update_user_memory(
    memory_id: str, req: MemoryRequest, user=Depends(current_user)
) -> dict:
    text, problem = memory_rules.validate(req.content, memory_rules.is_adult(user))
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    updated = db.update_memory(user["id"], memory_id, text)
    if updated is None:
        raise HTTPException(status_code=404, detail="That memory is already gone.")
    return _memory_json(updated)


@app.delete("/api/v1/memories/{memory_id}")
async def delete_user_memory(memory_id: str, user=Depends(current_user)) -> dict:
    if not db.delete_memory(user["id"], memory_id):
        raise HTTPException(status_code=404, detail="That memory is already gone.")
    return {"ok": True}


@app.delete("/api/v1/memories")
async def delete_all_user_memories(user=Depends(current_user)) -> dict:
    return {"ok": True, "deleted": db.delete_all_memories(user["id"])}


# ---------- Gmail (incremental consent) ----------

def _require_gmail() -> None:
    if not GMAIL_ENABLED:
        raise HTTPException(status_code=404, detail="Gmail is turned off")


@app.get("/api/v1/gmail/connect")
async def gmail_connect(user=Depends(current_user)) -> RedirectResponse:
    _require_gmail()
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
    _require_gmail()
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
    if grant is None or not GMAIL_ENABLED:
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
    _require_gmail()
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
            detail="That draft no longer exists — it may already have been sent",
        )
    except gmail.GmailApiError as exc:
        raise HTTPException(status_code=502, detail=f"Gmail: {exc}")
    return {"ok": True, **updated}


@app.post("/api/v1/gmail/drafts/{draft_id}/send")
async def gmail_send_draft(draft_id: str, user=Depends(current_user)) -> dict:
    """The only path that actually sends mail — driven by the user's button."""
    _require_gmail()
    try:
        sent = gmail.send_draft(user["id"], draft_id)
    except ServiceNotConnected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except gmail.DraftGone:
        raise HTTPException(
            status_code=409,
            detail="That draft no longer exists — it may already have been sent",
        )
    except gmail.GmailApiError as exc:
        raise HTTPException(status_code=502, detail=f"Gmail: {exc}")
    db.mark_draft_sent(user["id"], draft_id, sent.get("message_id", ""))
    return {"ok": True, **sent}


# ---------- Calendar (incremental consent) ----------

def _require_calendar() -> None:
    if not CALENDAR_ENABLED:
        raise HTTPException(status_code=404, detail="Google Calendar is turned off")


@app.get("/api/v1/calendar/connect")
async def calendar_connect(user=Depends(current_user)) -> RedirectResponse:
    _require_calendar()
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
    _require_calendar()
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
    if grant is None or not CALENDAR_ENABLED:
        return CalendarStatusResponse(connected=False)
    return CalendarStatusResponse(connected=True, scopes=grant["scopes"].split())


@app.post("/api/v1/calendar/disconnect")
async def calendar_disconnect(user=Depends(current_user)) -> dict:
    db.delete_google_grant(user["id"], "calendar")
    return {"ok": True}


@app.post("/api/v1/calendar/events/{event_id}/delete")
async def calendar_delete_event(event_id: str, user=Depends(current_user)) -> dict:
    """The only path that deletes an event — driven by the user's button.

    The assistant proposes the deletion in chat; nothing is removed until the
    user confirms here.
    """
    _require_calendar()
    try:
        calendar.delete_event(user["id"], event_id)
    except ServiceNotConnected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except calendar.EventGone:
        raise HTTPException(
            status_code=409,
            detail="That event is already gone — it may have been deleted already",
        )
    except calendar.CalendarApiError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar: {exc}")
    db.mark_event_deleted(user["id"], event_id)
    return {"ok": True, "event_id": event_id}


# ---------- chats ----------

@app.get("/api/v1/chats")
async def list_chats(user=Depends(current_user)) -> list[dict]:
    chats = db.list_chats(user["id"])
    _flag_tool_results(
        chats, "draft_email", "draft_id", db.get_sent_draft_ids(user["id"]), "sent"
    )
    _flag_tool_results(
        chats,
        "propose_event_deletion",
        "event_id",
        db.get_deleted_event_ids(user["id"]),
        "deleted",
    )
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
    # A worker thread: removing the chat's webpage container shells out to the CLI.
    if not await asyncio.to_thread(db.delete_chat, user["id"], chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"ok": True}


@app.patch("/api/v1/chats/{chat_id}/active-branch")
async def set_active_branch(
    chat_id: str, req: SetActiveBranchRequest, user=Depends(current_user)
) -> dict:
    """Remember the branch tail the user is viewing (synced across devices)."""
    leaf = (req.leaf_id or "").strip() or None
    if not db.set_chat_active_leaf(user["id"], chat_id, leaf):
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


# ---------- webpage workspaces (the canvas) ----------

# The most recent build attempt per chat, so the canvas can show a failed
# rebuild after a save or an AI run (`lastBuild`). Same best-effort,
# single-process state as _WEBPAGE_BUSY below: with several workers a notice
# may be missing or stale, which only ever means a banner that does not show.
_WEBPAGE_BUILDS: dict[str, dict] = {}

# One file may be read into the Code tab at a time; past this it is described
# (binary: true) instead of loaded. Mirrors the tools' 1 MB write cap.
WEBPAGE_FILE_READ_MAX = 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview_base(chat_id: str, site: dict) -> str:
    """The root-absolute URL prefix a chat's page is served from."""
    return preview_base({**site, "chat_id": chat_id})


def _record_build(chat_id: str, build: dict | None) -> None:
    """Remember the latest build attempt so the canvas can show it."""
    if build:
        _WEBPAGE_BUILDS[chat_id] = {**build, "at": _now_iso()}


async def _rebuild_site(chat_id: str, site: dict) -> None:
    """Rebuild a site that has a build step, recording the outcome.

    Best-effort by design: the file writes already landed on the host, so a
    dead container engine (or a failing build) is a canvas notice, never a
    failed save. Off the event loop because a cold npm build can take minutes
    and every chat stream shares this loop.
    """
    workspace = sitebox.workspace_dir(site["id"])
    if not sitebox.has_build(workspace):
        return
    try:
        ok, error = await asyncio.to_thread(sitebox.run_build, site)
    except sitebox.SiteboxUnavailable as exc:
        ok, error = False, str(exc)
    _record_build(chat_id, {"ok": ok, "error": error or None})


def _webpage_env_payload(chat_id: str, site: dict | None) -> dict:
    """The canvas-facing shape of one workspace (db row -> camelCase, no file text).

    `files` is the workspace tree as names only — the canvas fetches one file's
    text when the Code tab opens it. `serveRoot` is where the preview serves
    from, which is `dist` exactly when a build output exists.
    """
    if site is None:
        return {
            "webpageId": None,
            "files": [],
            "updatedAt": None,
            "activeVersionId": None,
            "previewUrl": None,
            "previewBase": None,
            "entry": "index.html",
            "serveRoot": "",
            "hasBuild": False,
            "lastBuild": _WEBPAGE_BUILDS.get(chat_id),
        }
    workspace = sitebox.workspace_dir(site["id"])
    preview_base = _preview_base(chat_id, site)
    return {
        "webpageId": site["id"],
        "files": webpage_git.changed_files(site)[:500],
        "updatedAt": site["updated_at"],
        "activeVersionId": site["active_commit"],
        "previewUrl": preview_base + "index.html",
        "previewBase": preview_base,
        "entry": "index.html",
        "serveRoot": "dist" if (workspace / "dist" / "index.html").is_file() else "",
        "hasBuild": sitebox.has_build(workspace),
        "lastBuild": _WEBPAGE_BUILDS.get(chat_id),
    }


@app.get("/api/v1/webpages/{chat_id}")
async def get_webpage(chat_id: str, user=Depends(current_user)) -> dict:
    """The chat's website workspace for the canvas.

    Returns {webpageId, files, updatedAt, activeVersionId, previewUrl,
    previewBase, entry, serveRoot, hasBuild, lastBuild}; a chat without a site
    yet returns the same keys empty so the canvas can show its empty state.
    User-scoped, and file text lives only on disk — never in this payload nor
    in the full-history sync payloads.
    """
    if db.get_chat(user["id"], chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return _webpage_env_payload(chat_id, db.get_webpage_site(user["id"], chat_id))


@app.get("/api/v1/webpages/{chat_id}/files/{path:path}")
async def get_webpage_file(chat_id: str, path: str, user=Depends(current_user)) -> dict:
    """One file of the workspace: its text, or its size and type when binary.

    The Code tab fetches a single file on demand, so the canvas never holds a
    whole file map. A name the write rules reject is a 400, a name that is not
    in the workspace a 404.
    """
    if db.get_chat(user["id"], chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    site = db.get_webpage_site(user["id"], chat_id)
    if site is None:
        raise HTTPException(status_code=404, detail="No webpage yet")
    clean = _webpage_path(path)
    if clean is None or blocked_segment(clean):
        raise HTTPException(status_code=400, detail="That file name isn't allowed.")
    target = resolve_under(sitebox.workspace_dir(site["id"]), clean)
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    size = target.stat().st_size
    if size <= WEBPAGE_FILE_READ_MAX:
        try:
            return {"path": clean, "content": target.read_text(encoding="utf-8")}
        except UnicodeDecodeError:
            pass
    return {
        "path": clean,
        "binary": True,
        "mime": webpage_mime(clean),
        "size": size,
    }


WEBPAGE_DOWNLOAD_MAX = 50 * 1024 * 1024


def _download_name(title: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:60]
    return f"{slug or 'website'}.zip"


def _zip_site(root: Path) -> bytes | None:
    """The site's raw files as a zip, or None past WEBPAGE_DOWNLOAD_MAX."""
    buffer = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(root.rglob("*")):
            if file.is_symlink() or not file.is_file():
                continue
            rel = file.relative_to(root).as_posix()
            # .gitignore is the workspace's own repo bookkeeping, not site content.
            if rel == ".gitignore" or blocked_segment(rel) or resolve_under(root, rel) is None:
                continue
            total += file.stat().st_size
            if total > WEBPAGE_DOWNLOAD_MAX:
                return None
            archive.write(file, rel)
    return buffer.getvalue()


@app.get("/api/v1/webpages/{chat_id}/download")
async def download_webpage(chat_id: str, user=Depends(current_user)) -> Response:
    """The site as a .zip for rehosting or sending.

    Raw files from the serve root (dist/ once built, else the workspace), so
    the zip carries none of the preview runtime or rebased URLs.
    """
    chat = db.get_chat(user["id"], chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    site = db.get_webpage_site(user["id"], chat_id)
    if site is None:
        raise HTTPException(status_code=404, detail="No webpage yet")
    root = serve_root(sitebox.workspace_dir(site["id"]))
    if not (root / "index.html").is_file():
        raise HTTPException(status_code=404, detail="No webpage yet")
    body = await asyncio.to_thread(_zip_site, root)
    if body is None:
        raise HTTPException(status_code=413, detail="This site is too big to download.")
    return Response(
        content=body,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{_download_name(chat["title"])}"'
        },
    )


@app.get("/api/v1/webpages/{chat_id}/versions")
async def list_webpage_iterations(chat_id: str, user=Depends(current_user)) -> dict:
    """Every iteration of the chat, oldest first, plus the active one's id.

    Summaries only (file text stays on disk): the canvas lists the history and
    fetches one iteration's diff on demand.
    """
    if db.get_chat(user["id"], chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    site = db.get_webpage_site(user["id"], chat_id)
    return {
        "activeVersionId": site["active_commit"] if site else None,
        "versions": webpage_git.list_versions(site) if site else [],
    }


@app.put("/api/v1/webpages/{chat_id}/active")
async def set_webpage_active_version(
    chat_id: str, req: SetWebpageVersionRequest, user=Depends(current_user)
) -> dict:
    """Activate one iteration: the canvas, later AI runs and manual edits use it.

    The working copy is checked out to that snapshot and everything with a
    build step is rebuilt from it, so the preview shows the iteration itself.
    """
    _webpage_idle_or_409(chat_id)
    if db.get_chat(user["id"], chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    site = db.get_webpage_site(user["id"], chat_id)
    if site is None or not webpage_git.is_version(site, req.version_id):
        raise HTTPException(status_code=404, detail="Iteration not found")
    webpage_git.checkout(site, req.version_id)
    db.set_webpage_active_commit(user["id"], chat_id, req.version_id)
    await _rebuild_site(chat_id, site)
    return _webpage_env_payload(chat_id, db.get_webpage_site(user["id"], chat_id))


@app.put("/api/v1/webpages/{chat_id}/files")
async def save_webpage_file(
    chat_id: str, req: SaveWebpageFileRequest, user=Depends(current_user)
) -> dict:
    """Save one hand-edited file as its own iteration.

    Same rules as the write_webpage_file tool (both call
    validate_webpage_write), so the messages match what the tool returns.
    `baseVersionId` is the iteration the tab rendered — a stale value is
    refused (409) instead of silently overwriting a newer iteration.
    """
    _webpage_idle_or_409(chat_id)
    if db.get_chat(user["id"], chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    site = db.get_webpage_site(user["id"], chat_id)
    if site is None:
        raise HTTPException(status_code=404, detail="No webpage yet")
    clean, problem = validate_webpage_write(req.path, req.content)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if req.base_version_id != site["active_commit"]:
        raise HTTPException(
            status_code=409,
            detail="The page changed since you opened it. Reload the canvas and try again.",
        )
    workspace = sitebox.workspace_dir(site["id"])
    target = resolve_under(workspace, clean)
    if target is None or blocked_segment(clean):
        raise HTTPException(status_code=400, detail="That file name isn't allowed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    webpage_git.commit_workspace(site, origin="manual", label=f"Edited {clean}", message_uid=None)
    await _rebuild_site(chat_id, site)
    return _webpage_env_payload(chat_id, db.get_webpage_site(user["id"], chat_id))


@app.get("/api/v1/webpages/{chat_id}/versions/{version_id}/diff")
async def get_webpage_iteration_diff(
    chat_id: str, version_id: str, against: str | None = None, user=Depends(current_user)
) -> dict:
    """Unified diff of one iteration against its parent (or `against`)."""
    if db.get_chat(user["id"], chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    site = db.get_webpage_site(user["id"], chat_id)
    diff = webpage_git.version_diff(site, version_id, against) if site else None
    if diff is None:
        raise HTTPException(status_code=404, detail="Iteration not found")
    return diff


# ---------- webpage preview (capability URL, no session) ----------
#
# What a path serves (dist/ or root, rebasing, the preview runtime) lives in
# app.webpage_preview, shared with the page checker.


def _preview_response(body: bytes, mime: str) -> Response:
    return Response(content=body, media_type=mime, headers=PREVIEW_HEADERS)


def _preview_404() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found", headers=PREVIEW_HEADERS)


@app.get("/api/v1/webpages/{chat_id}/preview/{key}/{path:path}")
async def webpage_preview(chat_id: str, key: str, path: str) -> Response:
    """Serve one file of a site to the sandboxed canvas iframe.

    Deliberately unauthenticated: `preview_key` is the capability, because the
    iframe has no session. See webpage_preview.resolve_preview for what each
    path serves.
    """
    site = db.get_webpage_site_by_chat(chat_id)
    if site is None or key != site["preview_key"]:
        raise _preview_404()
    served = resolve_preview(
        sitebox.workspace_dir(site["id"]), _preview_base(chat_id, site), path
    )
    if served is None:
        raise _preview_404()
    return _preview_response(*served)


# ---------- attachments (user uploads) ----------

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_ASSET_BYTES = 20 * 1024 * 1024


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
    to text here so read_attachment can serve it without re-parsing. Assets
    (audio, video, fonts) keep their bytes with no text — the model places them
    on a webpage with add_webpage_asset."""
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
    elif documents.is_asset(filename):
        if len(data) > MAX_ASSET_BYTES:
            raise HTTPException(status_code=413, detail="That file is too large (20MB max)")
        kind, text, mime = "asset", "", documents.mime_for(filename)
    else:
        raise HTTPException(
            status_code=415,
            detail="That file type isn't supported. Try an image, PDF, Word, "
            "PowerPoint, Excel, a text file, or a media/font file (audio, "
            "video, woff2).",
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
    preview source); documents download under their original name. Uploads are
    immutable (a new upload gets a new id), so we send an immutable
    Cache-Control. ``private`` keeps shared caches (for example Cloudflare)
    from storing it; it is a browser fallback only."""
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
        headers={"Cache-Control": "private, max-age=5184000, immutable"},
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


# Chats whose reply is being generated right now. Activating an iteration or
# saving a manual edit mid-run would race the agent's writes, so those routes
# refuse. In-process state, which is exact for this single-process server; run
# with several workers it degrades to best-effort (a race then just adds one
# extra iteration, never corrupt data — every write is snapshot-committed).
_WEBPAGE_BUSY: set[str] = set()


def _webpage_idle_or_409(chat_id: str) -> None:
    if chat_id in _WEBPAGE_BUSY:
        raise HTTPException(
            status_code=409, detail="A reply is still being written. Wait for it to finish."
        )


async def _tracked_events(chat_id: str, events):
    """Mark the chat busy for the lifetime of the SSE body (cleared on disconnect too)."""
    _WEBPAGE_BUSY.discard(chat_id)  # a previous run that leaked must not wedge the chat
    _WEBPAGE_BUSY.add(chat_id)
    try:
        async for event in events:
            yield event
    finally:
        _WEBPAGE_BUSY.discard(chat_id)


GRAPH_RECURSION_LIMIT = 75


def _graph_config(user, req: ChatRequest) -> RunnableConfig:
    """Invisible tool context: user id (auth scoping), chat id (so
    generate_image/image_edit can persist pictures against the current chat),
    and reply id (so every webpage write of one run lands as one iteration).

    The recursion limit counts graph steps (three per tool round): LangGraph's
    default of 25 ends a run after about eight tool calls, which a site with a
    few files, a test and a fix already needs."""
    return {
        "recursion_limit": GRAPH_RECURSION_LIMIT,
        "configurable": {
            "user_id": user["id"],
            "chat_id": req.chat_id,
            "reply_id": req.reply_id,
        }
    }


def _sync_history(chat_id: str, req: ChatRequest) -> str | None:
    """Full-tree sync: stored messages mirror the client's message tree.

    The client sends every node (``req.tree``) with its uid, parent link and
    sibling order; older clients that only send the visible path fall back to a
    single linear branch. Returns the uid of the last node on the visible path
    so a streamed reply can be parented to it. Idempotent across retries since
    the client resends the whole tree each turn.
    """
    tree = req.tree or req.messages
    history: list[dict] = []
    prev_uid: str | None = None
    for i, m in enumerate(tree):
        uid = m.id or f"legacy-{i}"
        history.append(
            {
                "uid": uid,
                "parent_uid": m.parent_id if m.id else prev_uid,
                "branch_index": m.branch_index or 0,
                "role": m.role,
                "content": m.content,
                "reasoning": m.reasoning or "",
                "tools": _redact_webpage_parts(m.tools or []),
                "parts": _redact_webpage_parts(m.parts or []),
                "thinking_done": m.thinking_done,
                "thought_seconds": m.thought_seconds,
            }
        )
        prev_uid = uid
    db.replace_chat_messages(chat_id, history)
    db.update_chat_meta(
        chat_id,
        mode=req.mode,
        subject=req.subject,
        clear_subject=req.subject is None,
    )
    if not req.messages:
        return None
    if not req.tree:
        # Legacy payload: the visible path is the whole linear history.
        return history[-1]["uid"] if history else None
    wanted = req.messages[-1].id
    if wanted:
        for node in history:
            if node["uid"] == wanted:
                return wanted
    return None


def _serialize_tool_calls(tool_calls: list[ToolCall]) -> list[dict]:
    return json.loads(json.dumps([t.model_dump() for t in tool_calls]))


@app.post("/api/v1/chat")
async def chat(req: ChatRequest, user=Depends(rate_limited_user)) -> ChatResponse:
    _owned_chat_or_404(user, req)
    _WEBPAGE_BUSY.discard(req.chat_id)
    _WEBPAGE_BUSY.add(req.chat_id)
    try:
        think_start = time.monotonic()
        result = await GRAPH.ainvoke(_state(req, user), config=_graph_config(user, req))
        thought_seconds = time.monotonic() - think_start
        # Fold whatever the run wrote into one iteration (and rebuild a site
        # that has a build step) before the reply is stored, so the iteration
        # the canvas shows is the one this turn produced.
        _, build = await asyncio.to_thread(
            sitebox.finalize_run, user["id"], req.chat_id, None
        )
        _record_build(req.chat_id, build)
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
    finally:
        _WEBPAGE_BUSY.discard(req.chat_id)


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
    reply_parent_uid = _sync_history(req.chat_id, req)
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
    # The model streams a tool call's name and arguments before the graph runs
    # it; on_tool_start only fires once the arguments are complete. We show the
    # card as soon as the name arrives so a big webpage write is visible while
    # its text streams, then claim it when the run starts. Keyed by the chunk's
    # tool-call index, reset each model turn.
    streamed_calls: dict[object, dict] = {}
    run_to_tool_id: dict[str, str] = {}
    # UI ids already handed out in this reply, so a provider that reuses a
    # tool-call id (or omits one) can't fold two cards together.
    used_tool_ids: set[str] = set()
    blocks = _ThinkingBlockTimer(parts)

    async for event in GRAPH.astream_events(
        _state(req, user),
        config=_graph_config(user, req),
        version="v2",
    ):
        kind = event["event"]
        if kind == "on_chat_model_start":
            # A streamed call the graph never ran (an unknown tool name) would
            # otherwise leave its card spinning: close it as failed.
            for info in streamed_calls.values():
                if not info.get("emitted") or info.get("bound"):
                    continue
                stalled_id = info["ui_id"]
                stalled_text = "This tool didn't run."
                tools_by_id[stalled_id]["ok"] = False
                tools_by_id[stalled_id]["result"] = stalled_text
                part = parts_by_tool_id.get(stalled_id)
                if part is not None:
                    part["ok"] = False
                    part["result"] = stalled_text
                yield _tool_result_event(stalled_id, info["name"], stalled_text, False)
            final_text = ""
            streamed_calls.clear()
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
            for tc in getattr(chunk, "tool_call_chunks", None) or []:
                index = tc.get("index")
                if index is None:
                    index = len(streamed_calls)
                info = streamed_calls.setdefault(
                    index,
                    {"id": None, "name": "", "emitted": False, "bound": False},
                )
                if tc.get("id"):
                    info["id"] = tc["id"]
                if tc.get("name"):
                    info["name"] += tc["name"]
                name = info["name"]
                if not name or info["emitted"]:
                    continue
                # The card appears now, while the arguments are still streaming.
                # Its args are filled in when the run actually starts.
                info["emitted"] = True
                ui_id = str(info["id"] or "")
                if not ui_id or ui_id in used_tool_ids:
                    ui_id = f"tool-call-{uuid4().hex}"
                used_tool_ids.add(ui_id)
                info["ui_id"] = ui_id
                args = _webpage_tool_args(name, {})
                blocks.open()
                tools_by_id[ui_id] = {
                    "id": ui_id,
                    "name": name,
                    "display": TOOL_DISPLAY_NAMES.get(name, name),
                    "args": args,
                    "result": "",
                    "ok": True,
                }
                part = {
                    "type": "tool",
                    "id": ui_id,
                    "name": name,
                    "display": TOOL_DISPLAY_NAMES.get(name, name),
                    "args": args,
                    "result": None,
                    "ok": None,
                }
                parts.append(part)
                parts_by_tool_id[ui_id] = part
                yield _tool_call_event(ui_id, name, args)
        elif kind == "on_tool_start":
            call_id = event.get("run_id", "")
            # A run whose parent is another tool run is the tool calling
            # itself internally — surface it once, as the outer call.
            nested = bool(set(event.get("parent_ids") or ()) & tool_run_ids)
            tool_run_ids.add(call_id)
            if nested:
                continue
            raw_args = event["data"].get("input", {}) or {}
            if not isinstance(raw_args, dict):
                raw_args = {"input": raw_args}
            name = event.get("name", "")
            args = _webpage_tool_args(name, raw_args)
            # Reuse the card already shown while the arguments streamed; fall
            # back to the run id for tools the model never streamed.
            ui_id = _claim_streamed_call(streamed_calls, name) or str(call_id)
            run_to_tool_id[call_id] = ui_id
            blocks.open()
            tools_by_id[ui_id] = {
                "id": ui_id,
                "name": name,
                "display": TOOL_DISPLAY_NAMES.get(name, name),
                "args": args,
                "result": "",
                "ok": True,
            }
            part = parts_by_tool_id.get(ui_id)
            if part is None:
                part = {
                    "type": "tool",
                    "id": ui_id,
                    "name": name,
                    "display": TOOL_DISPLAY_NAMES.get(name, name),
                    "args": args,
                    "result": None,
                    "ok": None,
                }
                parts.append(part)
                parts_by_tool_id[ui_id] = part
            else:
                part["name"] = name
                part["display"] = TOOL_DISPLAY_NAMES.get(name, name)
                part["args"] = args
            yield _tool_call_event(ui_id, name, args)
        elif kind == "on_tool_end":
            call_id = event.get("run_id", "")
            tool_id = run_to_tool_id.get(call_id, call_id)
            if tool_id not in tools_by_id:
                # A nested internal run we deliberately did not surface.
                continue
            output = event["data"].get("output", "")
            output_text = _tool_result_text(output)
            # A tool that failed comes back as an error ToolMessage rather than
            # an exception (see agent/graph.py's handler); keep the card honest.
            ok = getattr(output, "status", "success") != "error"
            tools_by_id[tool_id]["result"] = output_text
            tool_part = parts_by_tool_id.get(tool_id)
            if tool_part is not None:
                tool_part["result"] = output_text
                tool_part["ok"] = ok
            yield _tool_result_event(tool_id, event.get("name", ""), output_text, ok)
            image_part = _image_part_from_result(
                event.get("name", ""), output_text
            )
            if image_part is not None:
                parts.append(image_part)
                yield {"event": "image", "data": json.dumps(image_part)}
            webpage_part, _ = _webpage_result(output_text)
            if webpage_part is not None:
                _merge_webpage_part(parts, webpage_part)
                site = db.get_webpage_site(user["id"], req.chat_id)
                preview_base = _preview_base(req.chat_id, site) if site else None
                yield {
                    "event": "webpage",
                    "data": json.dumps(
                        {
                            "webpageId": webpage_part["webpageId"],
                            "files": webpage_part["files"],
                            "versionId": None,
                            "build": None,
                            "previewBase": preview_base,
                            "previewUrl": (
                                preview_base + "index.html" if preview_base else None
                            ),
                        }
                    ),
                }
        elif kind == "on_tool_error":
            call_id = event.get("run_id", "")
            tool_id = run_to_tool_id.get(call_id, call_id)
            if tool_id not in tools_by_id:
                continue
            error_text = str(event["data"].get("error", ""))
            tools_by_id[tool_id]["result"] = error_text
            tools_by_id[tool_id]["ok"] = False
            tool_part = parts_by_tool_id.get(tool_id)
            if tool_part is not None:
                tool_part["result"] = error_text
                tool_part["ok"] = False
            yield _tool_result_event(tool_id, event.get("name", ""), error_text, False)
    # The run is over: whatever it wrote becomes one iteration, and a site with
    # a build step is rebuilt from it. The final event carries the iteration id
    # and the build outcome — this is where the canvas learns a rebuild failed.
    sha, build = await asyncio.to_thread(
        sitebox.finalize_run, user["id"], req.chat_id, req.reply_id
    )
    _record_build(req.chat_id, build)
    if sha or build:
        site = db.get_webpage_site(user["id"], req.chat_id)
        preview_base = _preview_base(req.chat_id, site) if site else None
        yield {
            "event": "webpage",
            "data": json.dumps(
                {
                    "webpageId": site["id"] if site else None,
                    "files": webpage_git.changed_files(site) if site else [],
                    "versionId": sha,
                    "build": build,
                    "previewBase": preview_base,
                    "previewUrl": preview_base + "index.html" if preview_base else None,
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
            uid=req.reply_id,
            parent_uid=reply_parent_uid,
        )
        # The new reply is now the tail the user is viewing; remember it so the
        # same branch restores on reload and on other devices.
        if req.reply_id:
            db.set_chat_active_leaf(
                user["id"], req.chat_id, req.reply_id
            )


@app.post("/api/v1/chat/stream")
async def chat_stream(req: ChatRequest, user=Depends(rate_limited_user)):
    _owned_chat_or_404(user, req)
    return EventSourceResponse(_tracked_events(req.chat_id, _stream_chat_events(req, user)), ping=15)


# ---------- text-to-speech (read replies aloud) ----------


@app.post("/api/v1/tts")
async def start_tts(req: TtsRequest, user=Depends(rate_limited_user)) -> TtsJobResponse:
    """Register the text for speech synthesis. Returns a job id whose audio
    /api/v1/tts/{id} streams as audio/mpeg (fetchable once)."""
    if not config.TTS_ENABLED:
        raise HTTPException(status_code=503, detail="Text-to-speech is turned off")
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
