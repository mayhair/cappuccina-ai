import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app import calendar, db, google_oauth

CALENDAR_SCOPES = "https://www.googleapis.com/auth/calendar.events"


@pytest.fixture(autouse=True)
def _integrations_on(monkeypatch):
    """Both switches default to off; these tests cover the integration itself."""
    import app.main as main

    monkeypatch.setattr(main, "CALENDAR_ENABLED", True)
    monkeypatch.setattr(main, "GMAIL_ENABLED", True)


def _connect_calendar(client, monkeypatch):
    monkeypatch.setattr(
        google_oauth,
        "exchange_code",
        lambda code, uri: {
            "access_token": "cal-at",
            "refresh_token": "cal-rt",
            "expires_in": 3600,
            "scope": CALENDAR_SCOPES,
        },
    )
    start = client.get("/api/v1/calendar/connect", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    return client.get(
        f"/api/v1/calendar/callback?code=xyz&state={state}", follow_redirects=False
    )


def test_status_disconnected(signed_in):
    body = signed_in.get("/api/v1/calendar/status").json()
    assert body["connected"] is False
    assert body["scopes"] == []


def test_connect_status_and_disconnect(signed_in, monkeypatch):
    cb = _connect_calendar(signed_in, monkeypatch)
    assert "calendar=connected" in cb.headers["location"]

    status = signed_in.get("/api/v1/calendar/status").json()
    assert status["connected"] is True
    assert any("calendar.events" in s for s in status["scopes"])
    assert signed_in.get("/api/v1/auth/me").json()["calendarConnected"] is True

    assert signed_in.post("/api/v1/calendar/disconnect").json()["ok"] is True
    assert signed_in.get("/api/v1/calendar/status").json()["connected"] is False


def test_connect_requires_matching_state(signed_in):
    res = signed_in.get(
        "/api/v1/calendar/callback?code=xyz&state=bogus", follow_redirects=False
    )
    assert "calendar=error" in res.headers["location"]


def test_grants_are_independent(signed_in, monkeypatch):
    """Connecting Calendar must not touch the Gmail grant, and vice versa."""
    monkeypatch.setattr(
        google_oauth,
        "exchange_code",
        lambda code, uri: {
            "access_token": "gmail-at",
            "refresh_token": "gmail-rt",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        },
    )
    start = signed_in.get("/api/v1/gmail/connect", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    signed_in.get(f"/api/v1/gmail/callback?code=xyz&state={state}", follow_redirects=False)

    _connect_calendar(signed_in, monkeypatch)

    me = signed_in.get("/api/v1/auth/me").json()
    assert me["gmailConnected"] is True
    assert me["calendarConnected"] is True

    signed_in.post("/api/v1/gmail/disconnect")
    assert signed_in.get("/api/v1/gmail/status").json()["connected"] is False
    assert signed_in.get("/api/v1/calendar/status").json()["connected"] is True


def test_delete_event_success(signed_in, monkeypatch):
    _connect_calendar(signed_in, monkeypatch)
    monkeypatch.setattr(calendar, "delete_event", lambda uid, eid: {"id": eid})
    res = signed_in.post("/api/v1/calendar/events/e123/delete")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "event_id": "e123"}


def test_delete_event_already_gone(signed_in, monkeypatch):
    _connect_calendar(signed_in, monkeypatch)

    def gone(uid, eid):
        raise calendar.EventGone("not found")

    monkeypatch.setattr(calendar, "delete_event", gone)
    res = signed_in.post("/api/v1/calendar/events/e123/delete")
    assert res.status_code == 409
    assert "already gone" in res.json()["detail"]


def test_delete_event_requires_connection(signed_in):
    res = signed_in.post("/api/v1/calendar/events/e123/delete")
    assert res.status_code == 409


def test_deleted_event_survives_reload(signed_in, monkeypatch):
    _connect_calendar(signed_in, monkeypatch)
    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    proposal = {
        "status": "delete_proposed",
        "event_id": "e-1",
        "title": "Piano lesson",
        "when": "2026-09-22T16:00 → 17:00",
        "location": "",
    }
    result = json.dumps(proposal)
    db.append_chat_message(
        chat["id"],
        role="assistant",
        content="Want me to delete that?",
        parts=[
            {
                "type": "tool",
                "id": "t1",
                "name": "propose_event_deletion",
                "args": {},
                "result": result,
            }
        ],
        tools=[{"id": "t1", "name": "propose_event_deletion", "result": result}],
    )

    first = signed_in.get("/api/v1/chats").json()
    assert first[0]["messages"][0]["parts"][0].get("deleted") is not True

    monkeypatch.setattr(calendar, "delete_event", lambda uid, eid: {"id": eid})
    assert signed_in.post("/api/v1/calendar/events/e-1/delete").status_code == 200

    reloaded = signed_in.get("/api/v1/chats").json()
    message = reloaded[0]["messages"][0]
    assert message["parts"][0]["deleted"] is True
    assert message["tools"][0]["deleted"] is True
    user = db.get_user_by_email("kid@example.com")
    assert user is not None
    assert db.get_deleted_event_ids(user["id"]) == {"e-1"}


# ---------- client unit tests ----------


def _fake_request(monkeypatch, responses):
    """Capture calls and return canned responses keyed by (method, path)."""
    calls = []

    def fake(user_id, method, path, **kwargs):
        calls.append((method, path, kwargs))
        key = (method, path)
        if key in responses:
            return responses[key]
        return {}

    monkeypatch.setattr(calendar, "_request", fake)
    calendar._TZ_CACHE.clear()
    return calls


def test_list_events_defaults_to_a_week_and_reports_timezone(isolated_db, monkeypatch):
    calls = _fake_request(
        monkeypatch,
        {
            ("GET", "/events"): {
                "timeZone": "Europe/Berlin",
                "items": [
                    {
                        "id": "e1",
                        "summary": "Piano lesson",
                        "start": {"dateTime": "2026-09-22T16:00:00+02:00"},
                        "end": {"dateTime": "2026-09-22T17:00:00+02:00"},
                        "location": "Music room",
                    }
                ],
            },
        },
    )
    result = calendar.list_events("u1")
    assert result["timezone"] == "Europe/Berlin"
    assert result["events"][0]["title"] == "Piano lesson"
    assert result["events"][0]["id"] == "e1"
    _, _, kwargs = calls[-1]
    assert kwargs["params"]["singleEvents"] == "true"
    assert kwargs["params"]["orderBy"] == "startTime"
    assert "timeMin" in kwargs["params"] and "timeMax" in kwargs["params"]


def test_list_events_localizes_naive_bounds(isolated_db, monkeypatch):
    calls = _fake_request(
        monkeypatch,
        {
            ("GET", "/events"): {"timeZone": "Europe/Berlin", "items": []},
        },
    )
    calendar.list_events("u1", start="2026-09-22", end="2026-09-23")
    _, _, kwargs = calls[-1]
    assert kwargs["params"]["timeMin"].startswith("2026-09-22T00:00:00+02:00")
    assert kwargs["params"]["timeMax"].startswith("2026-09-23T23:59:59+02:00")


def test_create_event_timed_defaults_to_one_hour(isolated_db, monkeypatch):
    calls = _fake_request(
        monkeypatch,
        {
            ("GET", "/events"): {"timeZone": "UTC"},
            ("POST", "/events"): {
                "id": "new-1",
                "summary": "Swim practice",
                "start": {"dateTime": "2026-09-22T15:00:00+00:00"},
                "end": {"dateTime": "2026-09-22T16:00:00+00:00"},
            },
        },
    )
    event = calendar.create_event("u1", title="Swim practice", start="2026-09-22T15:00")
    assert event["id"] == "new-1"
    _, _, kwargs = calls[-1]
    body = kwargs["json"]
    assert body["start"] == {"dateTime": "2026-09-22T15:00:00", "timeZone": "UTC"}
    assert body["end"] == {"dateTime": "2026-09-22T16:00:00", "timeZone": "UTC"}
    assert "attendees" not in body


def test_create_event_all_day_ends_next_day(isolated_db, monkeypatch):
    calls = _fake_request(
        monkeypatch,
        {
            ("GET", "/events"): {"timeZone": "UTC"},
            ("POST", "/events"): {"id": "new-2", "summary": "No school"},
        },
    )
    calendar.create_event("u1", title="No school", start="2026-09-25")
    body = calls[-1][2]["json"]
    assert body["start"] == {"date": "2026-09-25"}
    assert body["end"] == {"date": "2026-09-26"}


def test_create_event_rejects_bad_date(isolated_db, monkeypatch):
    _fake_request(monkeypatch, {("GET", "/events"): {"timeZone": "UTC"}})
    try:
        calendar.create_event("u1", title="Oops", start="next tuesdayish")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "YYYY-MM-DDTHH:MM" in str(exc)


def test_update_event_patches_only_supplied_fields(isolated_db, monkeypatch):
    calls = _fake_request(
        monkeypatch,
        {
            ("GET", "/events"): {"timeZone": "UTC"},
            ("PATCH", "/events/e1"): {"id": "e1", "summary": "Renamed"},
        },
    )
    calendar.update_event("u1", "e1", title="Renamed")
    body = calls[-1][2]["json"]
    assert body == {"summary": "Renamed"}


def test_get_timezone_is_cached(isolated_db, monkeypatch):
    calls = _fake_request(monkeypatch, {("GET", "/events"): {"timeZone": "UTC"}})
    assert calendar.get_timezone("u-cache") == "UTC"
    assert calendar.get_timezone("u-cache") == "UTC"
    assert len(calls) == 1


# ---------- tool tests ----------


def test_calendar_tools_report_missing_connection(isolated_db):
    from agent import tools

    config = {"configurable": {"user_id": "nobody"}}
    assert "isn't connected" in tools.list_events.invoke({}, config=config).lower()
    assert (
        "isn't connected"
        in tools.create_event.invoke(
            {"title": "x", "start": "2026-09-22T10:00"}, config=config
        ).lower()
    )


def test_propose_deletion_returns_reviewable_json(isolated_db, monkeypatch):
    from agent import tools

    monkeypatch.setattr(
        calendar,
        "get_event",
        lambda uid, eid: {
            "id": eid,
            "title": "Piano lesson",
            "when": "2026-09-22T16:00 → 17:00",
            "location": "Music room",
        },
    )
    out = tools.propose_event_deletion.invoke(
        {"event_id": "e-9"}, config={"configurable": {"user_id": "u1"}}
    )
    data = json.loads(out)
    assert data["status"] == "delete_proposed"
    assert data["event_id"] == "e-9"
    assert data["title"] == "Piano lesson"


def test_calendar_tool_schemas_hide_injected_config():
    from agent import tools

    assert set(tools.list_events.args) == {"start", "end", "query", "max_results"}
    assert set(tools.create_event.args) == {
        "title",
        "start",
        "end",
        "description",
        "location",
    }
    assert set(tools.propose_event_deletion.args) == {"event_id"}


def test_event_gone_is_not_reported_as_success(isolated_db, monkeypatch):
    from agent import tools

    def gone(uid, eid, **kwargs):
        raise calendar.EventGone("nope")

    monkeypatch.setattr(calendar, "update_event", gone)
    out = tools.update_event.invoke(
        {"event_id": "e1", "title": "x"}, config={"configurable": {"user_id": "u1"}}
    )
    assert "no longer exists" in out.lower()
