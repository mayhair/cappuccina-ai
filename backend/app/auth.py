"""Session-cookie authentication helpers (see app/db.py for storage).

Sign-in happens through Google OAuth (app/google_oauth.py) or email +
password (the /api/v1/auth/register|login routes). This module hashes
passwords, mints/clears the cookie and resolves the current user from a session.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response

from app import db
from app.config import APP_ORIGIN

SESSION_COOKIE = "cappuccina_session"

PASSWORD_MIN_LENGTH = 8
# Argon2 hashes any length, but a cap keeps one request from costing much.
PASSWORD_MAX_LENGTH = 256

# Argon2id with argon2-cffi's defaults (RFC 9106 low-memory profile). Raising
# them later is safe: needs_rehash() upgrades each hash at its next sign-in.
_hasher = PasswordHasher()


# ---------- passwords ----------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    return _hasher.check_needs_rehash(stored)


def client_ip(request: Request) -> str:
    """The browser's IP. Production traffic arrives through the Cloudflare
    Tunnel, which sets CF-Connecting-IP; local dev falls back to the socket."""
    return request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else ""
    )


# ---------- session cookie ----------


def _cookie_params() -> dict:
    return {
        "key": SESSION_COOKIE,
        "httponly": True,
        "samesite": "lax",
        # Secure whenever the user browses over HTTPS (production); off for
        # local http dev so the cookie is still stored.
        "secure": APP_ORIGIN.startswith("https://"),
    }


def set_session_cookie(response: Response, token: str) -> None:
    # The cookie lives as long as the session can at most; the server enforces
    # the idle timeout, so the cookie never has to be re-sent on renewal.
    max_age = int(db.SESSION_MAX_AGE.total_seconds())
    response.set_cookie(value=token, max_age=max_age, **_cookie_params())


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(**_cookie_params())


def session_token(request: Request) -> str:
    return request.cookies.get(SESSION_COOKIE, "")


def current_user(request: Request):
    user = db.get_user_for_session(session_token(request))
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user
