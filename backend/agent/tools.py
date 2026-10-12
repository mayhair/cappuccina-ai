import base64
import json
import mimetypes
import os
import re
import secrets
from datetime import datetime
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from parallel import Parallel
from sympy import Eq, solve
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from agent import lorebook
from app import calendar, db, documents, gmail, sitebox, webpage_git
from app.config import (
    CHARACTER_REF_IMAGES,
    IMAGE_MODEL_ID,
    OPENAI_API_KEY,
    PARALLEL_API_KEY,
    SITE_EXEC_TIMEOUT,
)
from app.google_oauth import ServiceNotConnected


# ponytail: parse_expr is eval-adjacent; output is text-only so risk is low. Add a symbols whitelist if tool input ever becomes structured/persistent.
# The implicit-multiplication transform is what lets "7x" and "2(x+3)" parse.
_SYMTRANSFORMS = standard_transformations + (
    implicit_multiplication_application,
    convert_xor,
)


def _parse_math(text: str):
    return parse_expr(text, transformations=_SYMTRANSFORMS)


@tool
def solve_equation(expression: str) -> str:
    """Solve a math expression or equation. Accepts natural algebra such as "7x - 5 = 44", "2(x+3) = 14" or "1/4 + 1/8". Use for any math the user asks to compute or solve."""
    text = (expression or "").strip()
    if not text:
        return "I need a math expression or equation to work with."
    try:
        if "=" in text:
            left, right = text.split("=", 1)
            equation = Eq(_parse_math(left), _parse_math(right))
            symbols = sorted(equation.free_symbols, key=lambda s: s.name)
            if not symbols:
                return "That equation has no unknown to solve for."
            solutions = solve(equation, symbols[0])
            if not solutions:
                return "No solution found."
            return f"{symbols[0]} = " + ", ".join(str(s) for s in solutions)
        return str(_parse_math(text))
    except (SyntaxError, ValueError, TypeError, IndexError, ZeroDivisionError):
        return "I couldn't understand that math. Try writing it like '7x - 5 = 44' or '1/4 + 1/8'."
    except Exception:
        return "I couldn't solve that one."


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

# Resolutions the image tools may explicitly request. When the agent doesn't
# pick one, the tools send "auto" and let the image API choose the resolution.
IMAGE_SIZES = ("1024x1024", "1536x1024", "1024x1536")
AUTO_IMAGE_SIZE = "auto"

# How many character reference images one call may attach. The image API takes
# at most ten input images, and the source of an edit counts towards that.
MAX_CHARACTER_REFS = 4

_REF_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _normalized_size(requested: str = "") -> str:
    """A caller-supplied resolution, case- and separator-normalized."""
    return (requested or "").strip().lower().replace("×", "x")


def _image_size(requested: str = "") -> str:
    """The image API resolution: the caller's pick, or "auto" when none."""
    size = _normalized_size(requested)
    return size if size in IMAGE_SIZES else AUTO_IMAGE_SIZE


def _image_dimensions(size: str, raw: bytes = b"") -> tuple[int | None, int | None]:
    """Pixel width/height for an image's metadata.

    Prefers the encoded result, since "auto" lets the API pick the resolution;
    falls back to the requested resolution when the caller named one.
    """
    pixels = _image_pixels(raw)
    if pixels:
        return pixels
    if size in IMAGE_SIZES:
        width, height = size.split("x", 1)
        return int(width), int(height)
    return None, None


def _image_pixels(raw: bytes) -> tuple[int, int] | None:
    """Pixel width/height of an encoded image, or None if it can't be read."""
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            return image.width, image.height
    except Exception:
        return None


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
    images: list[tuple[str, bytes, str]],
    size: str,
    transparent_background: bool = False,
) -> tuple[bytes, str]:
    """Edit or redraw from reference images through the OpenAI image API.

    ``images`` are ``(filename, bytes, mime)`` in order: the first is the picture
    to edit (or the primary reference), the rest are extra references. One image
    is sent as the plain ``image`` field; several as the ``image[]`` array.
    httpx sets the multipart boundary itself, so Content-Type is left unset.
    """
    body = {"model": IMAGE_MODEL_ID, "prompt": prompt, "size": size}
    if transparent_background:
        body["background"] = "transparent"
    if len(images) == 1:
        files: dict | list = {"image": images[0]}
    else:
        files = [("image[]", image) for image in images]
    with httpx.Client(timeout=180) as client:
        resp = client.post(
            "https://api.openai.com/v1/images/edits",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            data=body,
            files=files,
        )
        resp.raise_for_status()
        data = (resp.json().get("data") or [{}])[0]
    return _image_bytes_from_response(data)


def _stored_source(
    user_id: str, image_id: str, attachment_id: str, *, image_only: bool = False
) -> tuple[bytes, str, str] | None:
    """Bytes, mime and filename for a stored picture or attachment, or None.

    Prefers a previously generated image (`image_id`); otherwise an uploaded
    attachment (`attachment_id`). With ``image_only`` a document attachment is
    a miss (the image tools can't edit one). Both lookups are user-scoped.
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
            if row is None or (image_only and row["kind"] != "image"):
                return None
            path = db.attachment_file_path(row["id"], row["filename"])
            return path.read_bytes(), row["mime"], row["filename"]
    except OSError:
        return None
    return None


def _character_refs(
    names: str,
) -> list[tuple[lorebook.Character, tuple[str, bytes, str]]]:
    """Reference images for the lore-book characters named in ``names``.

    ``names`` is a comma- or semicolon-separated list; each resolves through the
    lore book's forgiving matching. Characters without a reference file are
    skipped, duplicates are collapsed by id, and the list is capped at
    ``MAX_CHARACTER_REFS``. Unreadable files are skipped rather than failing the
    whole call. The assistant counts too, so naming her attaches her own look.
    """
    if not CHARACTER_REF_IMAGES:
        return []
    candidates = lorebook.reference_characters()
    refs: list[tuple[lorebook.Character, tuple[str, bytes, str]]] = []
    seen: set[str] = set()
    for raw_name in names.replace(";", ",").split(","):
        name = raw_name.strip()
        if not name:
            continue
        for character in lorebook.find(name, candidates):
            if character.id in seen or character.ref_path is None:
                continue
            mime = _REF_MIME_BY_SUFFIX.get(character.ref_path.suffix.lower())
            if mime is None:
                continue
            try:
                data = character.ref_path.read_bytes()
            except OSError:
                continue
            seen.add(character.id)
            refs.append((character, (character.ref_path.name, data, mime)))
            if len(refs) >= MAX_CHARACTER_REFS:
                return refs
    return refs


def _reference_prompt(prompt: str, names: list[str], editing: bool) -> str:
    """The scene prompt with an explicit note on which images are which."""
    listed = ", ".join(names)
    if editing:
        lead = (
            f"The first image is the picture to edit; the images after it are "
            f"the official reference looks for {listed}. "
        )
    else:
        lead = f"The provided images are the official reference looks for {listed}. "
    return (
        lead
        + "Keep every character's face, colours, proportions and outfit exactly "
        "consistent with its reference. " + prompt
    )


def _save_image_result(
    user_id: str,
    chat_id: str,
    raw: bytes,
    *,
    mime: str,
    prompt: str,
    size: str,
    transparent_background: bool,
    status: str,
    save_error: str,
    extra: dict | None = None,
) -> str:
    """Persist one generated/edited picture and return the model-facing JSON."""
    width, height = _image_dimensions(size, raw)
    try:
        image = db.create_image(
            user_id, chat_id, raw,
            mime=mime, width=width, height=height, prompt=prompt,
        )
    except OSError as exc:
        return f"Couldn't save {save_error} ({exc})."
    result: dict = {"status": status, "image_id": image["id"]}
    if extra:
        result.update(extra)
    result.update(
        {
            "prompt": prompt,
            "size": size,
            "background": "transparent" if transparent_background else "auto",
            "width": image["width"],
            "height": image["height"],
        }
    )
    return json.dumps(result)


@tool
def generate_image(
    prompt: str,
    config: RunnableConfig,
    size: str = "",
    transparent_background: bool = False,
    characters: str = "",
) -> str:
    """Draw an original picture from a text description and show it in the chat. Use this whenever the user asks you to draw, paint, sketch, or make a picture or image of something. Describe the scene vividly in `prompt`: subject, style, colors, mood. Optionally pick a `size`: "1024x1024" (square), "1536x1024" (wide), or "1024x1536" (tall) — match the subject's shape; leave it out to let the image model choose. When the picture shows one or more characters from your lore book, pass their names in `characters`, comma-separated (for example "Tralalero Tralala, Bombardiro Crocodilo"), so each keeps its official look; pass "Ballerina Cappuccina" for a picture of you, and leave it empty for anything else. Set `transparent_background` to true when the user wants a cut-out subject with no background (for example a sticker, logo, or icon) — an OpenAI-only option that returns a PNG with transparency. The picture is saved automatically and appears in the chat — tell the user it's ready. Never invent an image_id yourself; the returned JSON describes the saved picture."""
    user_id = _user_id(config)
    chat_id = _chat_id(config)
    if not user_id or not chat_id:
        return "Drawing isn't available right now."
    if not OPENAI_API_KEY:
        return "Drawing isn't set up yet."
    chosen_size = _image_size(size)
    refs = _character_refs(characters)
    try:
        if refs:
            raw, mime = _openai_edit_image(
                _reference_prompt(prompt, [c.name for c, _ in refs], editing=False),
                [image for _, image in refs],
                chosen_size,
                transparent_background,
            )
        else:
            raw, mime = _openai_generate_image(
                prompt, chosen_size, transparent_background
            )
    except (httpx.HTTPError, ValueError) as exc:
        return f"Couldn't draw that right now ({exc})."
    return _save_image_result(
        user_id,
        chat_id,
        raw,
        mime=mime,
        prompt=prompt,
        size=chosen_size,
        transparent_background=transparent_background,
        status="image_generated",
        save_error="the drawing",
        extra={"character_refs": [c.id for c, _ in refs]} if refs else None,
    )


@tool
def image_edit(
    prompt: str,
    config: RunnableConfig,
    image_id: str = "",
    attachment_id: str = "",
    size: str = "",
    transparent_background: bool = False,
    characters: str = "",
) -> str:
    """Edit or modify a picture that already exists and show the result in the chat. Use this when the user asks to change, restyle, add to, remove from, or otherwise alter an image. Pass exactly one source: `image_id` for a picture made earlier in this chat (the id from a generate_image or image_edit result), or `attachment_id` for an image the user attached. Describe the change in `prompt`. Only pass `characters` when the edit ADDS a new character from your lore book (or yourself, as "Ballerina Cappuccina") to the picture, comma-separated; if the characters already appear in the picture, or you are only restyling or changing something, leave it empty so the edit doesn't redraw them. Optionally set `size` to "1024x1024" (square), "1536x1024" (wide), or "1024x1536" (tall); omit it to let the image model choose. Set `transparent_background` to true when the user wants the result on a transparent background (for example removing a background to make a cut-out) — an OpenAI-only option that returns a PNG with transparency. Never invent ids yourself. The edited picture is saved as a new image and appears in the chat — tell the user it's ready. The returned JSON describes the saved picture."""
    user_id = _user_id(config)
    chat_id = _chat_id(config)
    if not user_id or not chat_id:
        return "Editing pictures isn't available right now."
    if not OPENAI_API_KEY:
        return "Editing pictures isn't set up yet."
    if not image_id and not attachment_id:
        return "I need to know which picture to edit."
    source = _stored_source(user_id, image_id, attachment_id, image_only=True)
    if source is None:
        return "I couldn't find that picture to edit."
    raw_source, mime, filename = source
    chosen_size = _image_size(size)
    refs = _character_refs(characters)
    call_prompt = prompt
    if refs:
        call_prompt = _reference_prompt(
            prompt, [c.name for c, _ in refs], editing=True
        )
    images = [(filename, raw_source, mime), *(image for _, image in refs)]
    try:
        raw, edited_mime = _openai_edit_image(
            call_prompt, images, chosen_size, transparent_background
        )
    except (httpx.HTTPError, ValueError) as exc:
        return f"Couldn't edit that picture right now ({exc})."
    return _save_image_result(
        user_id,
        chat_id,
        raw,
        mime=edited_mime,
        prompt=prompt,
        size=chosen_size,
        transparent_background=transparent_background,
        status="image_edited",
        save_error="the edited picture",
        extra={
            "source_image_id": image_id or None,
            "character_refs": [c.id for c, _ in refs] if refs else [],
        },
    )


# ---------- attachments (user uploads) ----------

# How much document text one read_attachment call returns; longer files are
# paged through with the `offset` argument.
ATTACHMENT_CHUNK = 20_000


@tool
def read_attachment(attachment_id: str, config: RunnableConfig, offset: int = 0) -> str:
    """Read the text of a file the user attached to this chat. Use the id shown in the message (for example, call this with the id from 'Attached file "report.pdf" (id=...)'). Long files come back in chunks: if the result says more remains, call again with offset set to where the previous read stopped. Only documents and text files have text; an image can be seen directly, and a media or font file (audio, video, woff2) is binary — put that on a webpage with add_webpage_asset instead."""
    user_id = _user_id(config)
    if not user_id:
        return "That file isn't available right now."
    row = db.get_attachment(user_id, attachment_id)
    if row is None:
        return "I couldn't find that attachment."
    if row["kind"] == "image":
        return f"{row['filename']} is an image — you can see it directly."
    if row["kind"] == "asset":
        return (
            f"{row['filename']} is a binary file ({row['mime']}, "
            f"{documents.human_size(row['size_bytes'])}) — its contents can't be "
            "read. To use it on the website, call add_webpage_asset with "
            f"attachment_id={row['id']}."
        )
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


# ---------- webpage builder (the canvas) ----------

# One path segment: letters, numbers, ".", "_" and "-". Anything else (a
# colon, a space, an empty segment) is refused, and ".." and ".git*" are
# refused by name below, so a path can never leave the chat's folder.
_WEBPAGE_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")

# A path may be ten folders deep; the site may keep any file type in them.
_WEBPAGE_PATH_SEGMENTS = 10

# One file's size ceiling and what read_webpage_file hands back in one piece.
_WEBPAGE_FILE_LIMIT = 1_000_000
_WEBPAGE_READ_LIMIT = 200_000
_WEBPAGE_LIST_LIMIT = 200
_WEBPAGE_ASSET_LIMIT = 20_000_000

_WEBPAGE_TIMEOUT_MAX = 600


def _webpage_path(path: str) -> str | None:
    """A safe relative path inside the chat's site folder, or None rejected.

    Backslashes become "/" so Windows-minded names still work. The path must
    not be absolute, must not climb out with "..", and must not touch .git.
    """
    clean = (path or "").strip().replace("\\", "/")
    if not clean or clean.startswith("/"):
        return None
    segments = clean.split("/")
    if len(segments) > _WEBPAGE_PATH_SEGMENTS:
        return None
    for segment in segments:
        if (
            not _WEBPAGE_SEGMENT.fullmatch(segment)
            or segment == ".."
            or segment.startswith(".git")
        ):
            return None
    return clean


def validate_webpage_write(path: str, content: str) -> tuple[str | None, str | None]:
    """Check one webpage file write; shared by the tool and the canvas save route.

    Returns ``(clean_path, error)`` with ``error`` None when the write is
    allowed. There is no per-site total: the files live on disk, so only the
    one file's size matters. The messages are what the tool returns and the
    route shows verbatim, so they live here only.
    """
    clean = _webpage_path(path)
    if clean is None:
        return None, (
            "That file name isn't allowed. Use a relative path like index.html, "
            "src/app.js or pages/about.astro."
        )
    if not content:
        return None, "That file is empty — write something into it."
    if len(content.encode("utf-8")) > _WEBPAGE_FILE_LIMIT:
        return None, "That file is too big. Keep each file under 1 MB."
    return clean, None


def _ensure_site(config: RunnableConfig) -> dict | None:
    """The chat's website registry row, folder and git repo, made on first use.

    None when the config carries no user or chat; each tool then answers with
    its own "not available right now" line. Parallel tool calls of one run all
    come through here, so the row is claimed atomically (db layer) and the repo
    is only initialised when it is missing (webpage_git serialises that).
    """
    user_id = _user_id(config)
    chat_id = _chat_id(config)
    if not user_id or not chat_id:
        return None
    site = db.get_or_create_webpage_site(user_id, chat_id, secrets.token_urlsafe(18))
    webpage_git.ensure_repo(sitebox.workspace_dir(site["id"]))
    return site


def _human_size(size: int) -> str:
    """A short file size for tool messages, like "812 KB" or "1.4 MB"."""
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.0f} KB"
    return f"{size} bytes"


@tool
def write_webpage_file(path: str, content: str, config: RunnableConfig) -> str:
    """Build or update a webpage in the user's live website workspace. Use this whenever the user asks you to make a webpage, website, landing page, game, quiz, or anything else that runs in a browser. Write one file per call: start with `index.html`, then add `styles.css` and `script.js` when they help, and use folders (`src/`, `pages/`, `img/` …) whenever they make the site clearer. Link files with RELATIVE paths — `<link rel="stylesheet" href="styles.css">`, `<script src="script.js"></script>` — because the page is served from the site's own root. Fonts and libraries come from https URLs. A picture the user attached, or one you drew with generate_image, goes into the site folder with add_webpage_asset; reference it by the name you gave it there (`<img src="cat.png">`). When the user asks for a real app, a framework or a build step, use webpage_shell instead. To change a page you already made, call read_webpage_file first to see the current text, then write the whole file back. The page appears live in the canvas as you write, so when you're finished tell the user it's ready. Never invent a webpage_id; the returned JSON describes what was written."""
    site = _ensure_site(config)
    if site is None:
        return "Building webpages isn't available right now."
    clean, problem = validate_webpage_write(path, content)
    if problem:
        return problem
    target = sitebox.workspace_dir(site["id"]) / clean
    os.makedirs(target.parent, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return json.dumps(
        {
            "status": "webpage_file_written",
            "webpage_id": site["id"],
            "version_id": None,
            "path": clean,
            "bytes": len(content.encode("utf-8")),
            "files": webpage_git.changed_files(site),
        }
    )


@tool
def read_webpage_file(path: str, config: RunnableConfig) -> str:
    """Read a file from the user's website workspace. Call this before editing a page you made earlier, so you change the current text instead of starting over. Returns the file's text; a picture comes back described rather than dumped, and an unknown name comes back as the list of files that do exist."""
    user_id = _user_id(config)
    chat_id = _chat_id(config)
    site = db.get_webpage_site(user_id, chat_id) if user_id and chat_id else None
    names = webpage_git.changed_files(site) if site else []
    if not names:
        return "There is no website in this chat yet — build one with write_webpage_file first."
    clean = _webpage_path(path)
    if clean is None or clean not in names:
        return (
            "No file named that. The website has: "
            + ", ".join(names[:_WEBPAGE_LIST_LIMIT])
        )
    data = (sitebox.workspace_dir(site["id"]) / clean).read_bytes()
    if len(data) > _WEBPAGE_READ_LIMIT:
        return f"That file is too big to read in one piece ({len(data)} bytes)."
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        mime = mimetypes.guess_type(clean)[0] or "application/octet-stream"
        return (
            f"{clean} is a picture in the site folder ({mime}, "
            f"{_human_size(len(data))}) — reference it from your page as {clean}."
        )


@tool
def add_webpage_asset(
    path: str, config: RunnableConfig, image_id: str = "", attachment_id: str = ""
) -> str:
    """Put a picture or file into the website's folder so the page can show or link it. Pass exactly one source: `image_id` for a picture you drew in this chat (the id from a generate_image or image_edit result), or `attachment_id` for a file the user attached — including media and font attachments you can't read, such as a song or a clip. `path` is the name the page uses — keep the file's own extension, for example "cat.png", "photo.jpg", "report.pdf", "song.mp3", "clip.mp4" or "fonts/app.woff2", in whatever folder you like — and reference it from the page with that same name: `<img src="cat.png">`, `background-image: url(img/cat.png)`, `<a href="report.pdf">`, `<audio controls src="song.mp3">`, `<video controls src="clip.mp4">` or `@font-face { src: url(fonts/app.woff2) }`. Use this whenever the user wants a picture or a file of theirs on the website; write the page with write_webpage_file first, then add the pictures and files it uses."""
    user_id = _user_id(config)
    chat_id = _chat_id(config)
    if not user_id or not chat_id:
        return "Webpage pictures aren't available right now."
    if not image_id and not attachment_id:
        return "I need to know which picture or file to add."
    if image_id and attachment_id:
        return "Pass either image_id or attachment_id, not both."
    source = _stored_source(user_id, image_id, attachment_id)
    if source is None:
        return "I couldn't find that picture or file."
    data, mime, filename = source
    suffix = PurePosixPath(filename).suffix.lower()
    clean = _webpage_path(path)
    if clean is None or PurePosixPath(clean).suffix.lower() != suffix:
        return (
            f"That name isn't allowed. Use a relative path ending in {suffix}, "
            f"like cat{suffix}."
        )
    if len(data) > _WEBPAGE_ASSET_LIMIT:
        return "That file is too big for the page folder (20 MB max)."
    site = db.get_webpage_site(user_id, chat_id)
    if site is None:
        return "There's no webpage yet — write index.html with write_webpage_file first."
    target = sitebox.workspace_dir(site["id"]) / clean
    if target.exists():
        return f"The site folder already has {clean}. Pick a different name."
    os.makedirs(target.parent, exist_ok=True)
    target.write_bytes(data)
    return json.dumps(
        {
            "status": "webpage_asset_added",
            "webpage_id": site["id"],
            "version_id": None,
            "path": clean,
            "mime": mime,
            "bytes": len(data),
            "files": webpage_git.changed_files(site),
            "assets": [],
        }
    )


@tool
def webpage_shell(command: str, config: RunnableConfig, timeout: int = SITE_EXEC_TIMEOUT) -> str:
    """Run a shell command inside the chat's website container. Use this to scaffold, install and build a real site instead of writing plain files by hand: bash, node, npm, git and curl are installed, and the command runs at /workspace — the site's own folder — so `mkdir`, `npm create vite@latest scaffold -- --template react` followed by `cp -a scaffold/. . && rm -rf scaffold`, `npm install` and `npm run build` all work, and anything they create lands in the canvas's files. A build only links its assets correctly in the canvas when its config sets `base: process.env.PREVIEW_BASE` (vite.config.js or astro.config.mjs): PREVIEW_BASE is already set in the container and is the only base that keeps the built site's asset URLs working there. `npm install` and `npm run build` are slow, so pass a larger `timeout` (up to 600 seconds) for them; the default (SITE_EXEC_TIMEOUT) is 120. If a build needs a tool outside node (a compiler, imagemagick, jq …), install it with `sudo apt-get install -y <package>` (run `sudo apt-get update` first when apt asks): sudo runs apt-get/apt/dpkg only, and the package lives in this site's container. The canvas serves `dist/` automatically once it exists and the workspace root otherwise, so rebuild after every change. Client-side routing must be hash-based (`#/page`) — the canvas preview is a sandboxed iframe where pushState URLs throw — while multi-page static output is fine. Do not run git yourself; the system versions every change automatically. The returned JSON has the exit code and the command's output."""
    site = _ensure_site(config)
    if site is None:
        return "The website workspace isn't available right now."
    try:
        exit_code, output = sitebox.run_command(
            site, command, max(1, min(_WEBPAGE_TIMEOUT_MAX, int(timeout)))
        )
    except sitebox.SiteboxUnavailable as exc:
        return str(exc)
    return json.dumps(
        {
            "status": "webpage_command_ran",
            "webpage_id": site["id"],
            "exit_code": exit_code,
            "output": output,
            "files": webpage_git.changed_files(site),
        }
    )


ALL_TOOLS = [solve_equation, web_search, read_webpage, lore_lookup, generate_image, image_edit, read_attachment, write_webpage_file, add_webpage_asset, read_webpage_file, webpage_shell, search_emails, read_email, draft_email, list_events, read_event, create_event, update_event, propose_event_deletion]

# Tools that only work once the user has linked that Google service. The graph
# leaves them out of the model's tool list until then, which saves their schema
# tokens on every call and keeps the model from calling a tool that can only fail.
GMAIL_TOOL_NAMES = frozenset({"search_emails", "read_email", "draft_email"})
CALENDAR_TOOL_NAMES = frozenset(
    {"list_events", "read_event", "create_event", "update_event", "propose_event_deletion"}
)

# Human-friendly names for UI display ("Called Tool: ...").
TOOL_DISPLAY_NAMES = {
    "solve_equation": "Calculator",
    "web_search": "Web Search",
    "read_webpage": "Read Webpage",
    "lore_lookup": "Lore Book",
    "generate_image": "Drawing",
    "image_edit": "Edit Picture",
    "read_attachment": "Read File",
    "write_webpage_file": "Building Webpage",
    "add_webpage_asset": "Adding to Webpage",
    "read_webpage_file": "Reading Webpage",
    "webpage_shell": "Running Site Command",
    "search_emails": "Inbox Search",
    "read_email": "Read Email",
    "draft_email": "Compose Email",
    "list_events": "Calendar Search",
    "read_event": "Read Event",
    "create_event": "New Event",
    "update_event": "Edit Event",
    "propose_event_deletion": "Delete Event",
    "fmhy_media_sites": "Free Media Sites",
    "fmhy_search": "Free Media Search",
    "fmhy_get_links": "Free Media Links",
    "fmhy_get_section": "Media Guide Section",
    "fmhy_list_sections": "Media Guide Categories",
}
