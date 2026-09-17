import json

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sympy import Eq, solve, sympify

from app import gmail
from app.config import BRAVE_API_KEY
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
def brave_search(query: str) -> str:
    """Search the web via Brave Search API. Use for current events, facts you don't know, or anything requiring up-to-date info."""
    with httpx.Client(timeout=10) as client:
        resp = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 3},
            headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        )
        resp.raise_for_status()
    results = resp.json().get("web", {}).get("results", [])
    if not results:
        return "No results found."
    return "\n\n".join(
        f"{r['title']}: {r.get('description', '')} ({r['url']})" for r in results
    )


# ---------- Gmail tools ----------
# The authenticated user id is injected by LangGraph through RunnableConfig;
# it never appears in the tool schema the model sees.

def _user_id(config: RunnableConfig | None) -> str:
    return ((config or {}).get("configurable", {}) or {}).get("user_id", "")


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


ALL_TOOLS = [solve_equation, brave_search, search_emails, read_email, draft_email]

# Human-friendly names for UI display ("Called Tool: ...").
TOOL_DISPLAY_NAMES = {
    "solve_equation": "Calculator",
    "brave_search": "Web Search",
    "search_emails": "Inbox Search",
    "read_email": "Read Email",
    "draft_email": "Compose Email",
}
