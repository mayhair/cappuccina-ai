from app.schemas import ChatRequest


def _request(timezone=None):
    return ChatRequest(chatId="c1", messages=[], timezone=timezone)


def test_resolve_timezone_prefers_client_value(isolated_db):
    import app.main as main

    assert main._resolve_timezone(_request("Europe/Berlin"), {"id": "u1"}) == "Europe/Berlin"


def test_resolve_timezone_rejects_unknown_zone(isolated_db):
    import app.main as main

    assert main._resolve_timezone(_request("Mars/Olympus"), {"id": "u1"}) == "UTC"


def test_resolve_timezone_defaults_to_utc_without_calendar(isolated_db):
    import app.main as main

    assert main._resolve_timezone(_request(), {"id": "u1"}) == "UTC"


def test_resolve_timezone_falls_back_to_connected_calendar(isolated_db, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "CALENDAR_ENABLED", True)
    monkeypatch.setattr(main.db, "get_google_grant", lambda uid, svc: {"service": svc})
    monkeypatch.setattr(main.calendar, "get_timezone", lambda uid: "America/New_York")

    assert main._resolve_timezone(_request(), {"id": "u1"}) == "America/New_York"


def test_resolve_timezone_ignores_calendar_while_switched_off(isolated_db, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "CALENDAR_ENABLED", False)
    monkeypatch.setattr(main.db, "get_google_grant", lambda uid, svc: {"service": svc})
    monkeypatch.setattr(main.calendar, "get_timezone", lambda uid: "America/New_York")

    assert main._resolve_timezone(_request(), {"id": "u1"}) == "UTC"
