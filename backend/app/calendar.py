"""Minimal Google Calendar API client over httpx (no google client library).

Every call takes the local user id and resolves a fresh access token through
app.google_oauth. We only ever touch the user's primary calendar.

Google's event times are either ``dateTime`` (RFC3339 with an offset, or a
naive local time plus the calendar's timezone) or ``date`` (all-day). The
helpers here accept the model's friendly forms and normalize them before the
request, preferring the calendar's own timezone for naive input.
"""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import google_http
from app.google_http import GoogleApiError

API_ROOT = "https://www.googleapis.com/calendar/v3/calendars/primary"
MAX_RESULTS_CAP = 20
DEFAULT_LOOKAHEAD_DAYS = 7

# Primary-calendar timezone rarely changes within a request burst; cache it for
# a minute so a tool call doesn't spend an extra round trip per event.
_TZ_CACHE: dict[str, tuple[datetime, str]] = {}
_TZ_CACHE_TTL = timedelta(minutes=1)

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CalendarApiError(GoogleApiError):
    """Non-2xx Google Calendar response."""


class EventGone(RuntimeError):
    """Event no longer exists — usually already deleted, here or in Google."""


def _request(user_id: str, method: str, path: str, **kwargs) -> dict:
    return google_http.request(
        user_id,
        method,
        path,
        service="calendar",
        api_root=API_ROOT,
        not_found="Not found in this Google Calendar",
        error_label="Calendar",
        error_cls=CalendarApiError,
        **kwargs,
    )


# ---------- time helpers ----------


def get_timezone(user_id: str) -> str:
    """The primary calendar's IANA timezone (e.g. 'Europe/Berlin').

    Read from an events.list probe rather than calendars.get: the narrow
    ``calendar.events`` scope allows listing events (and the response carries
    the calendar's ``timeZone``) but not fetching calendar metadata, which
    would need the broader ``calendar.readonly`` scope.
    """
    cached = _TZ_CACHE.get(user_id)
    now = datetime.now(timezone.utc)
    if cached and cached[0] > now:
        return cached[1]
    listing = _request(user_id, "GET", "/events", params={"maxResults": 1})
    tz = listing.get("timeZone") or "UTC"
    _TZ_CACHE[user_id] = (now + _TZ_CACHE_TTL, tz)
    return tz


def _parse_local(value: str) -> datetime | None:
    """Parse the model's datetime without letting a date-only value through."""
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _zone(tz: str) -> ZoneInfo:
    """Resolve an IANA name, falling back to UTC if the tz database is missing."""
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("UTC")


def _rfc3339(value: str, tz: str, *, end_of_day: bool = False) -> str:
    """An absolute RFC3339 timestamp for Calendar list query parameters.

    Accepts 'YYYY-MM-DD' (all-day bounds) or any ISO datetime; naive values are
    localized to the calendar's timezone, since timeMin/timeMax need an offset.
    """
    value = (value or "").strip()
    if _DATE_ONLY.match(value):
        try:
            d = date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"'{value}' is not a real date — use YYYY-MM-DD") from None
        hour, minute, second = (23, 59, 59) if end_of_day else (0, 0, 0)
        return datetime(
            d.year, d.month, d.day, hour, minute, second, tzinfo=_zone(tz)
        ).isoformat()
    parsed = _parse_local(value)
    if parsed is None:
        raise ValueError(
            f"couldn't read the time '{value}' — use YYYY-MM-DD or an RFC3339 "
            "timestamp like 2026-09-22T15:00:00+02:00"
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_zone(tz))
    return parsed.isoformat()


def _time_field(value: str, tz: str) -> dict:
    """Turn one model-supplied start/end into a Google event time object.

    Accepts 'YYYY-MM-DD' (all-day), 'YYYY-MM-DDTHH:MM' (local, in the
    calendar's timezone) or a full RFC3339 string with an offset/zone.
    Raises ValueError with a message the model can act on.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("empty time")
    if _DATE_ONLY.match(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"'{value}' is not a real date — use YYYY-MM-DD") from None
        return {"date": value}
    parsed = _parse_local(value)
    if parsed is None:
        raise ValueError(
            f"couldn't read the time '{value}' — use YYYY-MM-DDTHH:MM or an "
            "RFC3339 timestamp like 2026-09-22T15:00:00+02:00"
        )
    if parsed.tzinfo is None:
        return {"dateTime": parsed.isoformat(), "timeZone": tz}
    return {"dateTime": parsed.isoformat()}


def _is_all_day(event: dict) -> bool:
    return "date" in (event.get("start") or {})


def _summary_time(event: dict) -> str:
    start = event.get("start", {}) or {}
    end = event.get("end", {}) or {}
    if "date" in start:
        return f"all day {start['date']}"
    start_txt = start.get("dateTime", "?")
    end_txt = end.get("dateTime", "?")
    return f"{start_txt} → {end_txt}"


# ---------- public API ----------


def list_events(
    user_id: str,
    start: str = "",
    end: str = "",
    query: str = "",
    max_results: int = 10,
) -> dict:
    """List events in a window, defaulting to now → +7 days.

    Returns {"timezone", "start", "end", "events": [...]}. Times are returned
    exactly as Google stores them (ISO with offset) so the model can reason
    about them without guessing the user's zone.
    """
    tz = get_timezone(user_id)
    now = datetime.now(timezone.utc)

    time_min = _rfc3339(start, tz) if start.strip() else now.isoformat()
    time_max = (
        _rfc3339(end, tz, end_of_day=True)
        if end.strip()
        else (now + timedelta(days=DEFAULT_LOOKAHEAD_DAYS)).isoformat()
    )

    count = max(1, min(max_results or 10, MAX_RESULTS_CAP))
    params = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": count,
    }
    if query.strip():
        params["q"] = query.strip()

    listing = _request(user_id, "GET", "/events", params=params)
    events = []
    for item in listing.get("items", []) or []:
        events.append(
            {
                "id": item.get("id", ""),
                "title": item.get("summary", "(no title)"),
                "when": _summary_time(item),
                "all_day": _is_all_day(item),
                "location": item.get("location", ""),
                "recurring": bool(item.get("recurringEventId")),
            }
        )
    return {
        "timezone": tz,
        "start": time_min,
        "end": time_max,
        "events": events,
    }


def get_event(user_id: str, event_id: str, description_limit: int = 4000) -> dict:
    item = _request(user_id, "GET", f"/events/{event_id}")
    return {
        "id": item.get("id", event_id),
        "title": item.get("summary", "(no title)"),
        "when": _summary_time(item),
        "all_day": _is_all_day(item),
        "location": item.get("location", ""),
        "description": google_http.truncate(
            item.get("description", "") or "", description_limit
        ),
        "recurring": bool(item.get("recurringEventId")),
        "status": item.get("status", ""),
    }


def _event_body(
    *, title: str, start: str, end: str, description: str, location: str, tz: str
) -> dict:
    body: dict = {"summary": title or "(no title)"}
    start_field = _time_field(start, tz)
    body["start"] = start_field
    if end:
        body["end"] = _time_field(end, tz)
    elif "date" in start_field:
        end_date = date.fromisoformat(start_field["date"]) + timedelta(days=1)
        body["end"] = {"date": end_date.isoformat()}
    else:
        # _time_field already validated this as a parseable datetime.
        parsed_start = _parse_local(start_field["dateTime"])
        assert parsed_start is not None
        end_field = {"dateTime": (parsed_start + timedelta(hours=1)).isoformat()}
        if parsed_start.tzinfo is None:
            end_field["timeZone"] = tz
        body["end"] = end_field
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    # Attendees are deliberately never accepted: inviting guests would contact
    # other people, which the assistant is not allowed to do.
    return body


def create_event(
    user_id: str,
    *,
    title: str,
    start: str,
    end: str = "",
    description: str = "",
    location: str = "",
) -> dict:
    tz = get_timezone(user_id)
    body = _event_body(
        title=title,
        start=start,
        end=end,
        description=description,
        location=location,
        tz=tz,
    )
    item = _request(user_id, "POST", "/events", json=body)
    return {
        "id": item.get("id", ""),
        "title": item.get("summary", title),
        "when": _summary_time(item),
        "location": item.get("location", ""),
        "calendar_url": item.get("htmlLink", ""),
    }


def update_event(
    user_id: str,
    event_id: str,
    *,
    title: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    location: str = "",
) -> dict:
    """Patch only the fields the caller supplied (Google PATCH semantics)."""
    tz = get_timezone(user_id)
    patch: dict = {}
    if title:
        patch["summary"] = title
    if start:
        patch["start"] = _time_field(start, tz)
    if end:
        patch["end"] = _time_field(end, tz)
    if description:
        patch["description"] = description
    if location:
        patch["location"] = location
    if not patch:
        raise ValueError("nothing to change")
    item = _request(
        user_id, "PATCH", f"/events/{event_id}", json=patch, gone_cls=EventGone
    )
    return {
        "id": item.get("id", event_id),
        "title": item.get("summary", "(no title)"),
        "when": _summary_time(item),
        "location": item.get("location", ""),
    }


def delete_event(user_id: str, event_id: str) -> dict:
    """Permanently delete one event (Google's own UI offers a 30-day trash)."""
    _request(user_id, "DELETE", f"/events/{event_id}", gone_cls=EventGone)
    return {"id": event_id}
