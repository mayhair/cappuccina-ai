"""Shared httpx transport for Google's REST APIs (Gmail and Calendar).

Both are bearer-token JSON endpoints whose access token comes from
app.google_oauth; only the root URL, the OAuth service name and the error
wording differ. Each service module keeps a thin ``_request`` wrapper that
binds its own constants, so tests can still patch it per module.
"""

import httpx

from app.google_oauth import get_access_token

TIMEOUT = httpx.Timeout(20.0)


class GoogleApiError(RuntimeError):
    """A non-2xx Google REST response, carrying the HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… [truncated]"


def request(
    user_id: str,
    method: str,
    path: str,
    *,
    service: str,
    api_root: str,
    not_found: str,
    error_label: str,
    error_cls: type[GoogleApiError],
    gone_cls: type[RuntimeError] | None = None,
    **kwargs,
) -> dict:
    """Call one Google REST path and return its JSON (or {} for empty bodies).

    ``gone_cls`` turns a 404 into that "resource is gone" exception instead of
    the generic API error, for calls where 404 means the user already acted.
    """
    resp = httpx.request(
        method,
        f"{api_root}{path}",
        headers={"Authorization": f"Bearer {get_access_token(user_id, service)}"},
        timeout=TIMEOUT,
        **kwargs,
    )
    if resp.status_code == 404:
        if gone_cls is not None:
            raise gone_cls(not_found)
        raise error_cls(404, not_found)
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            detail = resp.text[:200]
        raise error_cls(
            resp.status_code, detail or f"{error_label} error {resp.status_code}"
        )
    return resp.json() if resp.content else {}
