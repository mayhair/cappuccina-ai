"""Minimal Gmail API client over httpx (no google-api-python-client needed).

Every call takes the local user id and resolves a fresh access token through
app.google_oauth. Gmail represents message bodies as nested MIME parts with
base64url-encoded data; the helpers here flatten that into plain text.
"""

import base64
import re
from email.message import EmailMessage
from html import unescape

import httpx

from app.google_oauth import get_access_token

API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT = httpx.Timeout(20.0)
MAX_RESULTS_CAP = 10

# Gmail search accepts an empty query as "everything"; keep it explicit.
DEFAULT_QUERY = "in:inbox"


class GmailApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class DraftGone(RuntimeError):
    """Draft no longer exists — almost always because it was already sent."""


def _headers(user_id: str) -> dict:
    return {"Authorization": f"Bearer {get_access_token(user_id, 'gmail')}"}


def _request(user_id: str, method: str, path: str, **kwargs) -> dict:
    resp = httpx.request(
        method,
        f"{API_ROOT}{path}",
        headers=_headers(user_id),
        timeout=TIMEOUT,
        **kwargs,
    )
    if resp.status_code == 404:
        raise GmailApiError(404, "Not found in this Gmail account")
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            detail = resp.text[:200]
        raise GmailApiError(
            resp.status_code, detail or f"Gmail error {resp.status_code}"
        )
    return resp.json() if resp.content else {}


# ---------- MIME helpers ----------


def _decode_b64url(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _headers_map(payload: dict) -> dict:
    return {
        h.get("name", "").lower(): h.get("value", "")
        for h in payload.get("headers", []) or []
    }


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text)


def _extract_body(payload: dict) -> tuple[str, bool]:
    """Return (text, is_html) preferring text/plain anywhere in the tree."""
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if mime == "text/plain" and data:
            plain.append(_decode_b64url(data))
        elif mime == "text/html" and data:
            html.append(_decode_b64url(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    if plain:
        return "\n".join(p for p in plain if p.strip()), False
    if html:
        return _strip_html("\n".join(html)).strip(), True
    return "", False


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… [truncated]"


# ---------- public API ----------


def search_messages(user_id: str, query: str, max_results: int = 5) -> list[dict]:
    query = (query or "").strip() or DEFAULT_QUERY
    count = max(1, min(max_results or 5, MAX_RESULTS_CAP))
    listing = _request(
        user_id,
        "GET",
        "/messages",
        params={"q": query, "maxResults": count},
    )
    summaries = []
    for ref in listing.get("messages", []) or []:
        detail = _request(
            user_id,
            "GET",
            f"/messages/{ref['id']}",
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "To", "Subject", "Date"],
            },
        )
        headers = _headers_map(detail.get("payload", {}) or {})
        summaries.append(
            {
                "id": detail.get("id", ref["id"]),
                "from": headers.get("from", ""),
                "to": headers.get("to", ""),
                "subject": headers.get("subject", "(no subject)"),
                "date": headers.get("date", ""),
                "snippet": detail.get("snippet", ""),
                "unread": "UNREAD" in (detail.get("labelIds") or []),
            }
        )
    return summaries


def get_message(user_id: str, message_id: str, body_limit: int = 4000) -> dict:
    detail = _request(
        user_id, "GET", f"/messages/{message_id}", params={"format": "full"}
    )
    payload = detail.get("payload", {}) or {}
    headers = _headers_map(payload)
    body, _ = _extract_body(payload)
    return {
        "id": detail.get("id", message_id),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date": headers.get("date", ""),
        "body": _truncate(body, body_limit) or "(no readable text body)",
    }


def create_draft(user_id: str, to: str, subject: str, body: str) -> dict:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    draft = _request(user_id, "POST", "/drafts", json={"message": {"raw": raw}})
    return {
        "draft_id": draft.get("id", ""),
        "message_id": (draft.get("message") or {}).get("id", ""),
        "to": to,
        "subject": subject,
        "body": body,
    }


def update_draft(user_id: str, draft_id: str, to: str, subject: str, body: str) -> dict:
    """Replace a draft's contents, preserving its thread if it has one."""
    try:
        existing = _request(
            user_id, "GET", f"/drafts/{draft_id}", params={"format": "metadata"}
        )
    except GmailApiError as exc:
        if exc.status == 404:
            raise DraftGone(str(exc)) from exc
        raise

    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    payload: dict = {"message": {"raw": raw}}
    thread_id = (existing.get("message") or {}).get("threadId")
    if thread_id:
        payload["message"]["threadId"] = thread_id
    _request(user_id, "PUT", f"/drafts/{draft_id}", json=payload)
    return {"draft_id": draft_id, "to": to, "subject": subject, "body": body}


def send_draft(user_id: str, draft_id: str) -> dict:
    # users.drafts.send is POST /drafts/send with the id in the body -- there is
    # no /drafts/{id}/send route (Gmail 404s on that, which looks like "gone").
    try:
        result = _request(user_id, "POST", "/drafts/send", json={"id": draft_id})
    except GmailApiError as exc:
        if exc.status == 404:
            raise DraftGone(str(exc)) from exc
        raise
    return {"message_id": result.get("id", ""), "thread_id": result.get("threadId", "")}
