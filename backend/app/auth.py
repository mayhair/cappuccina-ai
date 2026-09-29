"""Session-cookie authentication helpers (see app/db.py for storage).

Sign-in itself happens through Google OAuth (app/google_oauth.py); this module
only mints/clears the cookie and resolves the current user from a session.
"""

from fastapi import HTTPException, Request, Response

from app import db
from app.config import APP_ORIGIN

SESSION_COOKIE = "cappuccina_session"


def _cookie_params() -> dict:
    return {
        "key": SESSION_COOKIE,
        "httponly": True,
        "samesite": "strict",
        # Secure whenever the user browses over HTTPS (production); off for
        # local http dev so the cookie is still stored.
        "secure": APP_ORIGIN.startswith("https://"),
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
