"""Per-user rate limiting for the endpoints that cost money (LLM and TTS calls).

A sliding window held in memory: fine for a single-process deployment, and the
counts simply reset on restart.
"""

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException

from app.auth import current_user
from app.config import RATE_LIMIT_PER_MINUTE

WINDOW_SECONDS = 60

_hits: dict[str, deque[float]] = defaultdict(deque)


def check(user_id: str, limit: int | None = None, now: float | None = None) -> None:
    """Record one request for the user, or raise 429 when over the limit."""
    limit = RATE_LIMIT_PER_MINUTE if limit is None else limit
    now = time.monotonic() if now is None else now
    hits = _hits[user_id]
    while hits and now - hits[0] >= WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= limit:
        retry_after = max(1, int(WINDOW_SECONDS - (now - hits[0])) + 1)
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a moment and try again.",
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)


def rate_limited_user(user=Depends(current_user)):
    """Like current_user, but counts the request against the user's limit."""
    check(user["id"])
    return user


def reset() -> None:
    _hits.clear()
