"""Session-cookie authentication helpers (see app/db.py for storage)."""

import hashlib
import secrets

from fastapi import HTTPException, Request, Response

from app import db

SESSION_COOKIE = "cappuccina_session"
_PBKDF2_ITERATIONS = 200_000


# ---------- passwords ----------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    ).hex()
    return secrets.compare_digest(candidate, digest)


# ---------- session cookie ----------

def _cookie_params() -> dict:
    return {
        "key": SESSION_COOKIE,
        "httponly": True,
        "samesite": "lax",
        "secure": False,  # local dev; flip once served over HTTPS
    }


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(value=token, max_age=30 * 24 * 3600, **_cookie_params())


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(**_cookie_params())


def session_token(request: Request) -> str:
    return request.cookies.get(SESSION_COOKIE, "")


def current_user(request: Request):
    user = db.get_user_for_session(session_token(request))
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user
