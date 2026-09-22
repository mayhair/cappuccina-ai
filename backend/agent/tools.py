import base64
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from parallel import Parallel
from sympy import Eq, solve, sympify

from agent import lorebook
from app import calendar, db, gmail
from app.config import IMAGE_MODEL_ID, OPENAI_API_KEY, PARALLEL_API_KEY
from app.google_oauth import ServiceNotConnected


# ponytail: sympify is eval-adjacent; output is text-only so risk is low. Add a symbols whitelist if tool input ever becomes structured/persistent.
@tool
def solve_equation(expression: str) -> str:
    """Solve a math expression or equation. Use for any math the user asks to compute or solve."""
    if "=" in expression:
        left, right = expression.split("=", 1)
        solutions = solve(Eq(sympify(left), sympify(right)))
        return f"x = {solutions}" if solutions else "No solution found."
    return str(sympify(expression))


@tool
def web_search(query: str) -> str:
    """Search the web via the Parallel Search API. Use for current events, facts you don't know, or anything requiring up-to-date info."""
    client = Parallel(api_key=PARALLEL_API_KEY)
    try:
        response = client.search(search_queries=[query], mode="fast")
    except Exception as exc:
        return f"Couldn't search the web ({exc})."
    results = response.results or []
    if not results:
        return "No results found."
    return "\n\n".join(
        f"{r.title}: {' '.join(r.excerpts or [])[:500]} ({r.url})" for r in results
    )


@tool
def read_webpage(url: str, objective: str = "") -> str:
    """Read a specific web page. Use when you need more detail than web_search's excerpts, or when the user shares a URL. Pass `objective` to get only the parts of the page relevant to what you're looking for; without it you get the whole page as markdown."""
    client = Parallel(api_key=PARALLEL_API_KEY)
    try:
        if objective:
            response = client.extract(
                urls=[url],
                objective=objective,
                advanced_settings={"excerpt_settings": {"max_chars_per_result": 3000}},
            )
        else:
            response = client.extract(
                urls=[url],
                advanced_settings={"full_content": {"max_chars_per_result": 10000}},
            )
    except Exception as exc:
        return f"Couldn't read that page ({exc})."
    if response.errors:
        return f"Couldn't read that page ({response.errors[0].error_type})."
    results = response.results or []
    if not results:
        return "No readable content found at that URL."
    r = results[0]
    header = f"{r.title}: {r.url}" if r.title else r.url
    if r.publish_date:
        header += f" (published {r.publish_date})"
    body = "\n\n".join(r.excerpts or []) if objective else (r.full_content or "")
    if not body:
        return f"{header}\n\nNo readable content found."
    if not objective and len(body) >= 10000:
        body += "\n\n[Truncated — pass an `objective` to fetch only the relevant excerpts.]"
    return f"{header}\n\n{body}"


@tool
def lore_lookup(name: str) -> str:
    """Look up a character from Cappuccina's own world — the Italian brainrot universe she came from — in her lore book. Only use this for characters listed in the system prompt's lore book; for anyone else (other cartoons, real people, general knowledge) just answer normally and never mention the book. The system prompt lists everyone she knows. Call this before telling the user about one of them, then retell what you find in her own voice. Pass the name the user used; if it isn't in the book, the result says so and names the closest matches."""
    return lorebook.lookup(name)


# ---------- Gmail tools ----------
# The authenticated user id (and current chat id) are injected by LangGraph
# through RunnableConfig; they never appear in the tool schema the model sees.

def _user_id(config: RunnableConfig | None) -> str:
    return ((config or {}).get("configurable", {}) or {}).get("user_id", "")


def _chat_id(config: RunnableConfig | None) -> str:
    return ((config or {}).get("configurable", {}) or {}).get("chat_id", "")


# ---------- image generation ----------

# Resolutions the image tools may request. The first is the default when the
# agent doesn't pick one.
IMAGE_SIZES = ("1024x1024", "1536x1024", "1024x1536")
DEFAULT_IMAGE_SIZE = IMAGE_SIZES[0]


def _image_size(requested: str = "") -> str:
    """Validated resolution for an image API call, defaulting to the square."""
    size = (requested or "").strip().lower().replace("×", "x")
    return size if size in IMAGE_SIZES else DEFAULT_IMAGE_SIZE


def _image_dimensions(size: str = "") -> tuple[int | None, int | None]:
    """Pixel width/height for a resolution, for metadata."""
    try:
        width, height = _image_size(size).split("x", 1)
        return int(width), int(height)
    except (ValueError, AttributeError):
        return None, None


def _image_bytes_from_response(data: dict) -> tuple[bytes, str]:
    """Decode one image out of an OpenAI image API response entry."""
    if data.get("b64_json"):
        return base64.b64decode(data["b64_json"]), "image/png"
    if data.get("url"):
        with httpx.Client(timeout=60) as client:
            download = client.get(data["url"])
            download.raise_for_status()
        mime = download.headers.get("content-type", "image/png").split(";")[0].strip()
        return download.content, mime or "image/png"
    raise ValueError("the image API returned neither b64_json nor url")


def _openai_generate_image(
    prompt: str, size: str, transparent_background: bool = False
) -> tuple[bytes, str]:
    """Call the OpenAI image API. Returns (raw image bytes, mime type)."""
    body = {"model": IMAGE_MODEL_ID, "prompt": prompt, "size": size}
    if transparent_background:
        body["background"] = "transparent"
    with httpx.Client(timeout=120) as client:
        resp = client.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = (resp.json().get("data") or [{}])[0]
    return _image_bytes_from_response(data)


def _openai_edit_image(
    prompt: str,
    image_bytes: bytes,
    filename: str,
    mime: str,
    size: str,
    transparent_background: bool = False,
) -> tuple[bytes, str]:
    """Edit an existing image through the OpenAI image API (multipart upload).

    Returns (raw image bytes, mime type). httpx sets the multipart boundary
    itself, so Content-Type is deliberately left unset. A transparent
    background is an OpenAI-only option.
    """
    body = {"model": IMAGE_MODEL_ID, "prompt": prompt, "size": size}
    if transparent_background:
        body["background"] = "transparent"
    with httpx.Client(timeout=180) as client:
        resp = client.post(
            "https://api.openai.com/v1/images/edits",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            data=body,
            files={"image": (filename, image_bytes, mime)},
        )
        resp.raise_for_status()
        data = (resp.json().get("data") or [{}])[0]
    return _image_bytes_from_response(data)


def _load_edit_source(
    user_id: str, image_id: str, attachment_id: str
) -> tuple[bytes, str, str] | None:
    """Bytes, mime and filename for an image_edit source, or None if missing.

    Prefers a previously generated image (`image_id`); otherwise an uploaded
    image attachment (`attachment_id`). Both lookups are user-scoped.
    """
    try:
        if image_id:
            row = db.get_image(user_id, image_id)
            if row is None:
                return None
            path = db.image_file_path(row["id"], row["mime"])
            return path.read_bytes(), row["mime"], f"{row['id']}{path.suffix}"
        if attachment_id:
            row = db.get_attachment(user_id, attachment_id)
            if row is None or row["kind"] != "image":
                return None
            path = db.attachment_file_path(row["id"], row["filename"])
            return path.read_bytes(), row["mime"], row["filename"]
    except OSError:
        return None
    return None


@tool
def generate_image(
    prompt: str,
    config: RunnableConfig,
    size: str = "",
    transparent_background: bool = False,
) -> str:
    """Draw an original picture from a text description and show it in the chat. Use this whenever the user asks you to draw, paint, sketch, or make a picture or image of something. Describe the scene vividly in `prompt`: subject, style, colors, mood. Optionally pick a `size`: "1024x1024" (square, default), "1536x1024" (wide), or "1024x1536" (tall) — match the subject's shape. Set `transparent_background` to true when the user wants a cut-out subject with no background (for example a sticker, logo, or icon) — an OpenAI-only option that returns a PNG with transparency. The picture is saved automatically and appears in the chat — tell the user it's ready. Never invent an image_id yourself; the returned JSON describes the saved picture."""
    user_id = _user_id(config)
    chat_id = _chat_id(config)
    if not user_id or not chat_id:
        return "Drawing isn't available right now."
    if not OPENAI_API_KEY:
        return "Drawing isn't set up yet."
    chosen_size = _image_size(size)
    try:
        raw, mime = _openai_generate_image(prompt, chosen_size, transparent_background)
    except (httpx.HTTPError, ValueError) as exc:
        return f"Couldn't draw that right now ({exc})."
    width, height = _image_dimensions(chosen_size)
    try:
        image = db.create_image(
            user_id, chat_id, raw,
            mime=mime, width=width, height=height, prompt=prompt,
        )
    except OSError as exc:
        return f"Couldn't save the drawing ({exc})."
    return json.dumps(
        {
            "status": "image_generated",
            "image_id": image["id"],
            "prompt": prompt,
            "size": chosen_size,
            "background": "transparent" if transparent_background else "auto",
            "width": image["width"],
            "height": image["height"],
        }
    )


@tool
def image_edit(
    prompt: str,
    config: RunnableConfig,
    image_id: str = "",
    attachment_id: str = "",
    size: str = "",
    transparent_background: bool = False,
) -> str:
    """Edit or modify a picture that already exists and show the result in the chat. Use this when the user asks to change, restyle, add to, remove from, or otherwise alter an image. Pass exactly one source: `image_id` for a picture made earlier in this chat (the id from a generate_image or image_edit result), or `attachment_id` for an image the user attached. Describe the change in `prompt`. Optionally set `size` to "1024x1024" (square, default), "1536x1024" (wide), or "1024x1536" (tall); omit it to keep the configured default. Set `transparent_background` to true when the user wants the result on a transparent background (for example removing a background to make a cut-out) — an OpenAI-only option that returns a PNG with transparency. Never invent ids yourself. The edited picture is saved as a new image and appears in the chat — tell the user it's ready. The returned JSON describes the saved picture."""
    user_id = _user_id(config)
    chat_id = _chat_id(config)
    if not user_id or not chat_id:
        return "Editing pictures isn't available right now."
    if not OPENAI_API_KEY:
        return "Editing pictures isn't set up yet."
    if not image_id and not attachment_id:
        return "I need to know which picture to edit."
    source = _load_edit_source(user_id, image_id, attachment_id)
    if source is None:
        return "I couldn't find that picture to edit."
    raw_source, mime, filename = source
    chosen_size = _image_size(size)
    try:
        raw, edited_mime = _openai_edit_image(
            prompt, raw_source, filename, mime, chosen_size, transparent_background
        )
    except (httpx.HTTPError, ValueError) as exc:
        return f"Couldn't edit that picture right now ({exc})."
    width, height = _image_dimensions(chosen_size)
    try:
        image = db.create_image(
            user_id, chat_id, raw,
            mime=edited_mime, width=width, height=height, prompt=prompt,
        )
    except OSError as exc:
        return f"Couldn't save the edited picture ({exc})."
    return json.dumps(
        {
            "status": "image_edited",
            "image_id": image["id"],
            "source_image_id": image_id or None,
            "prompt": prompt,
            "size": chosen_size,
            "background": "transparent" if transparent_background else "auto",
            "width": image["width"],
            "height": image["height"],
        }
    )


# ---------- attachments (user uploads) ----------

# How much document text one read_attachment call returns; longer files are
# paged through with the `offset` argument.
ATTACHMENT_CHUNK = 20_000


@tool
def read_attachment(attachment_id: str, config: RunnableConfig, offset: int = 0) -> str:
    """Read the text of a file the user attached to this chat. Use the id shown in the message (for example, call this with the id from 'Attached file "report.pdf" (id=...)'). Long files come back in chunks: if the result says more remains, call again with offset set to where the previous read stopped."""
    user_id = _user_id(config)
    if not user_id:
        return "That file isn't available right now."
    row = db.get_attachment(user_id, attachment_id)
    if row is None:
        return "I couldn't find that attachment."
    if row["kind"] != "document":
        return f"{row['filename']} is an image — you can see it directly."
    text = row["text"] or ""
    if not text:
        return f"{row['filename']} has no readable text."
    total = len(text)
    start = max(0, offset)
    if start >= total:
        return f"You've reached the end of {row['filename']} ({total} characters total)."
    end = min(total, start + ATTACHMENT_CHUNK)
    header = f"{row['filename']} ({total} characters, showing {start}-{end}):\n\n"
    footer = (
        f"\n\n[More remains — call read_attachment again with offset={end}.]"
        if end < total
        else ""
    )
    return header + text[start:end] + footer


def _gmail_unavailable(config: RunnableConfig | None) -> str | None:
    if not _user_id(config):
        return "Gmail isn't available right now."
    return None


@tool
def search_emails(query: str, config: RunnableConfig, max_results: int = 5) -> str:
    """Search the user's Gmail inbox. Uses Gmail search syntax (e.g. 'from:teacher@school.edu', 'is:unread', 'subject:homework', 'newer_than:7d'). Returns sender, subject, date and a short preview for each match. Use read_email with an id to read the full message."""
    missing = _gmail_unavailable(config)
    if missing:
        return missing
    try:
        results = gmail.search_messages(_user_id(config), query, max_results)
    except ServiceNotConnected:
        return "Gmail isn't connected yet. Ask the user to connect it in Settings, then try again."
    except gmail.GmailApiError as exc:
        return f"Couldn't search the inbox ({exc})."
    if not results:
        return "No emails matched that search."
    lines = [
        f"[{m['id']}] From: {m['from']} | Subject: {m['subject']} | {m['date']}"
        + (" (unread)" if m["unread"] else "")
        + (f"\n  {m['snippet']}" if m["snippet"] else "")
        for m in results
    ]
    return "\n".join(lines)


@tool
def read_email(message_id: str, config: RunnableConfig) -> str:
    """Read one email in full by its id (the bracketed id from search_emails). Returns the sender, subject, date and body text."""
    missing = _gmail_unavailable(config)
    if missing:
        return missing
    try:
        message = gmail.get_message(_user_id(config), message_id)
    except ServiceNotConnected:
        return "Gmail isn't connected yet. Ask the user to connect it in Settings, then try again."
    except gmail.GmailApiError as exc:
        return f"Couldn't read that email ({exc})."
    return (
        f"From: {message['from']}\nTo: {message['to']}\n"
        f"Subject: {message['subject']}\nDate: {message['date']}\n\n{message['body']}"
    )


@tool
def draft_email(to: str, subject: str, body: str, config: RunnableConfig) -> str:
    """Write an email as a draft in the user's Gmail. This NEVER sends: the user reviews the draft and presses Send themselves. Use this whenever the user asks you to write, reply to, or send an email. Tell the user the draft is ready to review. If it's a reply, read the original first and quote it in the body."""
    missing = _gmail_unavailable(config)
    if missing:
        return missing
    try:
        draft = gmail.create_draft(_user_id(config), to, subject, body)
    except ServiceNotConnected:
        return "Gmail isn't connected yet. Ask the user to connect it in Settings, then try again."
    except gmail.GmailApiError as exc:
        return f"Couldn't create the draft ({exc})."
    return json.dumps({"status": "draft_created", **draft})


ALL_TOOLS = [solve_equation, web_search, read_webpage, generate_image, image_edit, read_attachment, search_emails, read_email, draft_email]


# ---------- Calendar tools ----------

CALENDAR_NOT_CONNECTED = (
    "Google Calendar isn't connected yet. Ask the user to connect it in "
    "Settings, then try again."
)


def _calendar_unavailable(config: RunnableConfig | None) -> str | None:
    if not _user_id(config):
        return "Google Calendar isn't available right now."
    return None


def _calendar_today(tz: str) -> str:
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("UTC")
    return datetime.now(zone).strftime("%A %d %B %Y")


def _format_event_line(event: dict) -> str:
    line = f"[{event['id']}] {event['title']} — {event['when']}"
    if event.get("location"):
        line += f" (at {event['location']})"
    if event.get("recurring"):
        line += " (repeating)"
    return line


@tool
def list_events(config: RunnableConfig, start: str = "", end: str = "", query: str = "", max_results: int = 10) -> str:
    """Look at the user's Google Calendar. Returns the events in a date range, each with an id you can pass to read_event, update_event or propose_event_deletion. With no arguments it lists the next 7 days. Use this whenever the user asks what's on their calendar, what they have coming up, or whether they're free. Dates use the user's own calendar timezone, which is stated in the result."""
    missing = _calendar_unavailable(config)
    if missing:
        return missing
    try:
        days = calendar.list_events(
            _user_id(config), start, end, query, max_results
        )
    except ServiceNotConnected:
        return CALENDAR_NOT_CONNECTED
    except ValueError as exc:
        return f"I couldn't understand that date ({exc})."
    except calendar.CalendarApiError as exc:
        return f"Couldn't read the calendar ({exc})."
    header = (
        f"Today is {_calendar_today(days['timezone'])} in the user's calendar "
        f"timezone ({days['timezone']}). Showing {days['start']} → {days['end']}."
    )
    if not days["events"]:
        return f"{header}\n\nNo events in that range."
    return header + "\n" + "\n".join(_format_event_line(e) for e in days["events"])


@tool
def read_event(event_id: str, config: RunnableConfig) -> str:
    """Read one calendar event in full by its id (the bracketed id from list_events). Gives the title, time, location and any notes. Use this before changing an event so you know what it currently says."""
    missing = _calendar_unavailable(config)
    if missing:
        return missing
    try:
        event = calendar.get_event(_user_id(config), event_id)
    except ServiceNotConnected:
        return CALENDAR_NOT_CONNECTED
    except calendar.CalendarApiError as exc:
        return f"Couldn't read that event ({exc})."
    parts = [
        f"{event['title']} — {event['when']}",
    ]
    if event.get("location"):
        parts.append(f"Where: {event['location']}")
    if event.get("recurring"):
        parts.append("This is part of a repeating series.")
    if event.get("description"):
        parts.append(f"Notes: {event['description']}")
    return "\n".join(parts)


@tool
def create_event(title: str, start: str, config: RunnableConfig, end: str = "", description: str = "", location: str = "") -> str:
    """Add a new event to the user's Google Calendar. `start` and `end` are either 'YYYY-MM-DDTHH:MM' for a timed event or 'YYYY-MM-DD' for an all-day event; leave `end` out to default to one hour later (or one day for all-day). Tell the user what you added. Never add guests or invite other people."""
    missing = _calendar_unavailable(config)
    if missing:
        return missing
    try:
        event = calendar.create_event(
            _user_id(config),
            title=title,
            start=start,
            end=end,
            description=description,
            location=location,
        )
    except ServiceNotConnected:
        return CALENDAR_NOT_CONNECTED
    except ValueError as exc:
        return f"I couldn't set that time ({exc})."
    except calendar.CalendarApiError as exc:
        return f"Couldn't add that event ({exc})."
    return json.dumps({"status": "event_created", **event})


@tool
def update_event(event_id: str, config: RunnableConfig, title: str = "", start: str = "", end: str = "", description: str = "", location: str = "") -> str:
    """Change an existing calendar event. Only pass the fields that need changing — everything else stays as it was. Read the event first so you can tell the user exactly what changed. Same date formats as create_event."""
    missing = _calendar_unavailable(config)
    if missing:
        return missing
    try:
        event = calendar.update_event(
            _user_id(config),
            event_id,
            title=title,
            start=start,
            end=end,
            description=description,
            location=location,
        )
    except ServiceNotConnected:
        return CALENDAR_NOT_CONNECTED
    except calendar.EventGone:
        return "That event no longer exists — it may already have been deleted."
    except ValueError as exc:
        return f"I couldn't make that change ({exc})."
    except calendar.CalendarApiError as exc:
        return f"Couldn't change that event ({exc})."
    return json.dumps({"status": "event_updated", **event})


@tool
def propose_event_deletion(event_id: str, config: RunnableConfig) -> str:
    """Ask the user to confirm deleting one calendar event. This NEVER deletes anything by itself: it shows the user a card and they press Delete themselves. Use this whenever the user asks to cancel, remove or delete an event. Tell the user you've prepared the deletion for them to confirm."""
    missing = _calendar_unavailable(config)
    if missing:
        return missing
    try:
        event = calendar.get_event(_user_id(config), event_id)
    except ServiceNotConnected:
        return CALENDAR_NOT_CONNECTED
    except calendar.CalendarApiError as exc:
        return f"Couldn't find that event ({exc})."
    return json.dumps(
        {
            "status": "delete_proposed",
            "event_id": event["id"],
            "title": event["title"],
            "when": event["when"],
            "location": event["location"],
        }
    )


ALL_TOOLS = [solve_equation, web_search, read_webpage, lore_lookup, generate_image, image_edit, read_attachment, search_emails, read_email, draft_email, list_events, read_event, create_event, update_event, propose_event_deletion]

# Human-friendly names for UI display ("Called Tool: ...").
TOOL_DISPLAY_NAMES = {
    "solve_equation": "Calculator",
    "web_search": "Web Search",
    "read_webpage": "Read Webpage",
    "lore_lookup": "Lore Book",
    "generate_image": "Drawing",
    "image_edit": "Edit Picture",
    "read_attachment": "Read File",
    "search_emails": "Inbox Search",
    "read_email": "Read Email",
    "draft_email": "Compose Email",
    "list_events": "Calendar Search",
    "read_event": "Read Event",
    "create_event": "New Event",
    "update_event": "Edit Event",
    "propose_event_deletion": "Delete Event",
}
