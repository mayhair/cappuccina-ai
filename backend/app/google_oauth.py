"""Google OAuth 2.0 (authorization code flow) and token storage helpers.

Two flows use this module:

* Sign-in  — openid/email/profile scopes, handled by /api/v1/auth/google/*.
* Gmail    — incremental consent for the Gmail scopes, /api/v1/gmail/*.

Access and refresh tokens are encrypted at rest with a Fernet key derived from
TOKEN_ENC_KEY (any string; ``openssl rand -hex 32`` works).
"""

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app import db
from app.config import (
    APP_ORIGIN,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    TOKEN_ENC_KEY,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# Refresh a little early so a token can't expire mid-request.
_EXPIRY_SKEW = timedelta(seconds=60)

TIMEOUT = httpx.Timeout(15.0)


class GoogleNotConfigured(RuntimeError):
    """GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are missing."""


class GmailNotConnected(RuntimeError):
    """The user has no usable Google grant (never granted, or revoked)."""


class GoogleOAuthError(RuntimeError):
    """Token endpoint returned an error we can't recover from."""


def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def login_redirect_uri() -> str:
    return f"{APP_ORIGIN}/api/v1/auth/google/callback"


def gmail_redirect_uri() -> str:
    return f"{APP_ORIGIN}/api/v1/gmail/callback"


# ---------- token encryption ----------

def _fernet() -> Fernet:
    material = TOKEN_ENC_KEY or GOOGLE_CLIENT_SECRET
    if not material:
        raise GoogleNotConfigured(
            "Set TOKEN_ENC_KEY (and Google credentials) before storing tokens"
        )
    # Accept raw strings, hex, or already-valid Fernet keys uniformly.
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(token: str) -> str:
    if not token:
        return ""
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(blob: str) -> str:
    if not blob:
        return ""
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise GoogleOAuthError("Stored Google token could not be decrypted") from exc


# ---------- authorization code flow ----------

def build_auth_url(
    state: str,
    scopes: list[str],
    redirect_uri: str,
    *,
    prompt: str = "select_account",
    offline: bool = False,
) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "prompt": prompt,
        "include_granted_scopes": "true",
    }
    if offline:
        params["access_type"] = "offline"
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _post_token(data: dict) -> dict:
    resp = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            **data,
        },
        timeout=TIMEOUT,
    )
    payload = resp.json() if resp.content else {}
    if resp.status_code >= 400 or "error" in payload:
        raise GoogleOAuthError(
            f"{payload.get('error', 'token_error')}: {payload.get('error_description', '')}".strip()
        )
    return payload


def exchange_code(code: str, redirect_uri: str) -> dict:
    return _post_token(
        {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    )


def fetch_userinfo(access_token: str) -> dict:
    resp = httpx.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ---------- grants ----------

def _expiry_from(payload: dict) -> str | None:
    expires_in = payload.get("expires_in")
    if not expires_in:
        return None
    return (
        datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    ).isoformat()


def store_grant(user_id: str, payload: dict) -> None:
    scopes = payload.get("scope", "")
    db.save_google_grant(
        user_id,
        scopes=scopes,
        access_token=encrypt_token(payload.get("access_token", "")),
        refresh_token=encrypt_token(payload.get("refresh_token", "")),
        expires_at=_expiry_from(payload),
    )


def get_access_token(user_id: str) -> str:
    """Return a currently-valid access token, refreshing if needed.

    Raises GmailNotConnected when the user has no grant or consent was revoked.
    """
    grant = db.get_google_grant(user_id)
    if grant is None:
        raise GmailNotConnected("Gmail is not connected")

    expires_at = grant["expires_at"]
    if expires_at and datetime.fromisoformat(expires_at) - _EXPIRY_SKEW > datetime.now(
        timezone.utc
    ):
        return decrypt_token(grant["access_token"])

    refresh_token = decrypt_token(grant["refresh_token"])
    if not refresh_token:
        db.delete_google_grant(user_id)
        raise GmailNotConnected("Gmail needs to be reconnected")

    try:
        payload = _post_token(
            {"refresh_token": refresh_token, "grant_type": "refresh_token"}
        )
    except GoogleOAuthError:
        # invalid_grant / expired consent: drop the grant so the UI re-prompts.
        db.delete_google_grant(user_id)
        raise GmailNotConnected("Gmail access expired — reconnect in Settings") from None

    # Refresh responses omit refresh_token; save_google_grant keeps the old one.
    db.save_google_grant(
        user_id,
        scopes=grant["scopes"],
        access_token=encrypt_token(payload.get("access_token", "")),
        refresh_token="",
        expires_at=_expiry_from(payload),
    )
    return payload.get("access_token", "")
