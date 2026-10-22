"""Gmail and Calendar are switched off by default (GMAIL_ENABLED/CALENDAR_ENABLED)."""

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
