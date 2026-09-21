import base64
import json

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from parallel import Parallel
from sympy import Eq, solve, sympify

from app import db, gmail
from app.config import IMAGE_MODEL_ID, IMAGE_SIZE, OPENAI_API_KEY, PARALLEL_API_KEY
from app.google_oauth import GmailNotConnected


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


# ---------- Gmail tools ----------
# The authenticated user id (and current chat id) are injected by LangGraph
# through RunnableConfig; they never appear in the tool schema the model sees.

def _user_id(config: RunnableConfig | None) -> str:
    return ((config or {}).get("configurable", {}) or {}).get("user_id", "")


def _chat_id(config: RunnableConfig | None) -> str:
    return ((config or {}).get("configurable", {}) or {}).get("chat_id", "")


# ---------- image generation ----------

def _image_dimensions() -> tuple[int | None, int | None]:
    """Requested pixel size from config (e.g. '1024x1024'), for metadata."""
    try:
        width, height = IMAGE_SIZE.lower().split("x", 1)
        return int(width), int(height)
    except (ValueError, AttributeError):
        return None, None


def _openai_generate_image(prompt: str) -> tuple[bytes, str]:
    """Call the OpenAI image API. Returns (raw image bytes, mime type)."""
    with httpx.Client(timeout=120) as client:
        resp = client.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": IMAGE_MODEL_ID, "prompt": prompt, "size": IMAGE_SIZE},
        )
        resp.raise_for_status()
        data = (resp.json().get("data") or [{}])[0]
    if data.get("b64_json"):
        return base64.b64decode(data["b64_json"]), "image/png"
    if data.get("url"):
        with httpx.Client(timeout=60) as client:
            download = client.get(data["url"])
            download.raise_for_status()
        mime = download.headers.get("content-type", "image/png").split(";")[0].strip()
        return download.content, mime or "image/png"
    raise ValueError("the image API returned neither b64_json nor url")


@tool
def generate_image(prompt: str, config: RunnableConfig) -> str:
    """Draw an original picture from a text description and show it in the chat. Use this whenever the user asks you to draw, paint, sketch, or make a picture or image of something. Describe the scene vividly in `prompt`: subject, style, colors, mood. The picture is saved automatically and appears in the chat — tell the user it's ready. Never invent an image_id yourself; the returned JSON describes the saved picture."""
    user_id = _user_id(config)
    chat_id = _chat_id(config)
    if not user_id or not chat_id:
        return "Drawing isn't available right now."
    if not OPENAI_API_KEY:
        return "Drawing isn't set up yet."
    try:
        raw, mime = _openai_generate_image(prompt)
    except (httpx.HTTPError, ValueError) as exc:
        return f"Couldn't draw that right now ({exc})."
    width, height = _image_dimensions()
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
    except GmailNotConnected:
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
    except GmailNotConnected:
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
    except GmailNotConnected:
        return "Gmail isn't connected yet. Ask the user to connect it in Settings, then try again."
    except gmail.GmailApiError as exc:
        return f"Couldn't create the draft ({exc})."
    return json.dumps({"status": "draft_created", **draft})


ALL_TOOLS = [solve_equation, web_search, read_webpage, generate_image, read_attachment, search_emails, read_email, draft_email]

# Human-friendly names for UI display ("Called Tool: ...").
TOOL_DISPLAY_NAMES = {
    "solve_equation": "Calculator",
    "web_search": "Web Search",
    "read_webpage": "Read Webpage",
    "generate_image": "Drawing",
    "read_attachment": "Read File",
    "search_emails": "Inbox Search",
    "read_email": "Read Email",
    "draft_email": "Compose Email",
}
