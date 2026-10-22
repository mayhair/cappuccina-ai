"""Gmail and Calendar are switched off by default (GMAIL_ENABLED/CALENDAR_ENABLED).

While off, the routes 404, stored grants are ignored, and startup deletes them.
"""

import pytest

from app import google_oauth


@pytest.fixture(autouse=True)
def _integrations_off(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "GMAIL_ENABLED", False)
    monkeypatch.setattr(main, "CALENDAR_ENABLED", False)


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/v1/gmail/connect"),
        ("get", "/api/v1/gmail/callback?code=x&state=y"),
        ("post", "/api/v1/gmail/drafts/d1/send"),
        ("get", "/api/v1/calendar/connect"),
        ("get", "/api/v1/calendar/callback?code=x&state=y"),
        ("post", "/api/v1/calendar/events/e1/delete"),
    ],
)
def test_routes_answer_404(signed_in, method, path):
    res = getattr(signed_in, method)(path, follow_redirects=False)
    assert res.status_code == 404


def test_update_draft_answers_404(signed_in):
    res = signed_in.patch(
        "/api/v1/gmail/drafts/d1", json={"to": "a@b.com", "subject": "", "body": "x"}
    )
    assert res.status_code == 404


def test_stored_grants_are_ignored(signed_in):
    user_id = signed_in.get("/api/v1/auth/me").json()["id"]
    google_oauth.store_grant(user_id, "gmail", {"access_token": "g", "scope": "g"})
    google_oauth.store_grant(user_id, "calendar", {"access_token": "c", "scope": "c"})

    me = signed_in.get("/api/v1/auth/me").json()
    assert me["gmailEnabled"] is False and me["calendarEnabled"] is False
    assert me["gmailConnected"] is False and me["calendarConnected"] is False
    assert signed_in.get("/api/v1/gmail/status").json()["connected"] is False
    assert signed_in.get("/api/v1/calendar/status").json()["connected"] is False


def test_chat_state_hides_the_tools(signed_in):
    import app.main as main
    from app.schemas import ChatRequest

    user_id = signed_in.get("/api/v1/auth/me").json()["id"]
    google_oauth.store_grant(user_id, "gmail", {"access_token": "g", "scope": "g"})
    google_oauth.store_grant(user_id, "calendar", {"access_token": "c", "scope": "c"})
    user = main.db.get_user_by_id(user_id)

    state = main._state(ChatRequest(chatId="c1", messages=[], timezone="UTC"), user)
    assert state["gmail"] is False and state["calendar"] is False


def test_disconnect_still_clears_old_grants(signed_in):
    user_id = signed_in.get("/api/v1/auth/me").json()["id"]
    google_oauth.store_grant(user_id, "gmail", {"access_token": "g", "scope": "g"})

    assert signed_in.post("/api/v1/gmail/disconnect").json()["ok"] is True
    assert google_oauth.db.get_google_grant(user_id, "gmail") is None


def _grant(user_id, service, token):
    google_oauth.store_grant(
        user_id, service, {"access_token": f"{token}-at", "refresh_token": f"{token}-rt", "scope": "s"}
    )


def _record_revokes(monkeypatch):
    revoked = []
    monkeypatch.setattr(
        google_oauth.httpx, "post", lambda url, data, timeout: revoked.append(data["token"])
    )
    return revoked


def test_purge_deletes_and_revokes_switched_off_grants(signed_in, monkeypatch):
    revoked = _record_revokes(monkeypatch)
    user_id = signed_in.get("/api/v1/auth/me").json()["id"]
    _grant(user_id, "gmail", "gm")
    _grant(user_id, "calendar", "cal")

    assert google_oauth.purge_grants(["gmail", "calendar"]) == 2
    assert google_oauth.db.list_google_grants(user_id) == []
    assert sorted(revoked) == ["cal-rt", "gm-rt"]


def test_purge_keeps_google_access_for_a_service_still_on(signed_in, monkeypatch):
    revoked = _record_revokes(monkeypatch)
    user_id = signed_in.get("/api/v1/auth/me").json()["id"]
    _grant(user_id, "gmail", "gm")
    _grant(user_id, "calendar", "cal")

    assert google_oauth.purge_grants(["gmail"]) == 1
    assert google_oauth.db.get_google_grant(user_id, "gmail") is None
    assert google_oauth.db.get_google_grant(user_id, "calendar") is not None
    # Revoking would also end the calendar grant, which Google ties to it.
    assert revoked == []


def test_purge_survives_a_failed_revoke(signed_in, monkeypatch):
    def boom(url, data, timeout):
        raise google_oauth.httpx.ConnectError("down")

    monkeypatch.setattr(google_oauth.httpx, "post", boom)
    user_id = signed_in.get("/api/v1/auth/me").json()["id"]
    _grant(user_id, "gmail", "gm")

    assert google_oauth.purge_grants(["gmail"]) == 1
    assert google_oauth.db.get_google_grant(user_id, "gmail") is None


def test_purge_with_nothing_off_is_a_no_op(signed_in):
    user_id = signed_in.get("/api/v1/auth/me").json()["id"]
    _grant(user_id, "gmail", "gm")

    assert google_oauth.purge_grants([]) == 0
    assert google_oauth.db.get_google_grant(user_id, "gmail") is not None


def test_startup_purges_switched_off_services(signed_in, monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient

    _record_revokes(monkeypatch)
    monkeypatch.setattr(main, "CALENDAR_ENABLED", True)

    async def no_fmhy():
        return []

    monkeypatch.setattr(main, "load_fmhy_tools", no_fmhy)
    monkeypatch.setattr(main.db, "close_pool", lambda: None)
    user_id = signed_in.get("/api/v1/auth/me").json()["id"]
    _grant(user_id, "gmail", "gm")
    _grant(user_id, "calendar", "cal")

    with TestClient(main.app):
        pass

    assert google_oauth.db.get_google_grant(user_id, "gmail") is None
    assert google_oauth.db.get_google_grant(user_id, "calendar") is not None
