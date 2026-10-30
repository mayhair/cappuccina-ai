"""Cloudflare Turnstile: server-side check of the token the sign-up form sends.

https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
"""

import httpx

from app.config import TURNSTILE_SECRET_KEY

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT = httpx.Timeout(10.0)


def is_configured() -> bool:
    return bool(TURNSTILE_SECRET_KEY)


def verify(token: str, remote_ip: str = "") -> bool:
    """True when Cloudflare accepts the token (or Turnstile isn't configured).

    A network failure counts as a failed check: sign-up is refused rather than
    let through unguarded, and the user can simply try again.
    """
    if not is_configured():
        return True
    if not token:
        return False
    data = {"secret": TURNSTILE_SECRET_KEY, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        resp = httpx.post(SITEVERIFY_URL, data=data, timeout=TIMEOUT)
        return bool(resp.json().get("success"))
    except (httpx.HTTPError, ValueError):
        return False
