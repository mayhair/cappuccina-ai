"""Text-to-speech for reading assistant replies aloud (Fish Audio).

POST /api/v1/tts registers the text and returns a job id; GET /api/v1/tts/{id}
then streams the MP3 straight from Fish Audio, so the browser's <audio>
element starts playing while synthesis is still running. Two steps are needed
because browsers can't feed a POST body into <audio>, and chat messages are
too long to pass as a GET query string.

A job may be fetched exactly once: the pending registry only maps ids to
their text (in memory, pruned by age and count), so there is nothing to
clean up once the audio has been streamed.
"""

import time
import uuid

import httpx

from app import config

FISH_TTS_URL = "https://api.fish.audio/v1/tts"
# Unclaimed jobs are dropped after this long; a client that never issues the
# GET must not leak memory.
PENDING_TTL = 300.0
MAX_PENDING = 64


class UpstreamError(Exception):
    """Fish Audio rejected the request or the connection failed."""


_pending: dict[str, dict] = {}


def _prune() -> None:
    now = time.monotonic()
    stale = [key for key, job in _pending.items() if now - job["created"] > PENDING_TTL]
    for key in stale:
        del _pending[key]
    while len(_pending) > MAX_PENDING:
        oldest = min(_pending, key=lambda key: _pending[key]["created"])
        del _pending[oldest]


def create_job(user_id: str, text: str) -> str:
    """Register text for synthesis; the id can be fetched once via /tts/{id}."""
    job_id = uuid.uuid4().hex
    _pending[job_id] = {"user_id": user_id, "text": text, "created": time.monotonic()}
    _prune()
    return job_id


def pop_job(job_id: str, user_id: str) -> str | None:
    """Return the text behind a valid, not-yet-fetched job owned by user_id."""
    job = _pending.get(job_id)
    if job is None or job["user_id"] != user_id:
        return None
    del _pending[job_id]
    return job["text"]


async def open_audio_stream(text: str):
    """Open the Fish Audio TTS request. Returns (client, response); the caller
    must drain response via audio_chunks(), which closes both afterwards."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(15, read=120))
    request = client.build_request(
        "POST",
        FISH_TTS_URL,
        headers={
            "Authorization": f"Bearer {config.FISH_API_KEY}",
            "model": config.FISH_TTS_MODEL,
        },
        json={
            "text": text,
            "reference_id": config.FISH_TTS_REFERENCE_ID,
            "format": "mp3",
            "latency": "balanced",
            "normalize": True,
        },
    )
    try:
        response = await client.send(request, stream=True)
    except (httpx.HTTPError, OSError) as exc:
        await client.aclose()
        raise UpstreamError(f"TTS connection failed: {exc}") from exc
    if response.status_code != 200:
        body = (await response.aread()).decode("utf-8", "replace")
        await response.aclose()
        await client.aclose()
        raise UpstreamError(
            f"Fish Audio returned {response.status_code}: {body[:200]}"
        )
    return client, response


async def audio_chunks(client: httpx.AsyncClient, response: httpx.Response):
    """Yield MP3 bytes from an open stream, closing it when done."""
    try:
        async for chunk in response.aiter_bytes():
            if chunk:
                yield chunk
    finally:
        await response.aclose()
        await client.aclose()