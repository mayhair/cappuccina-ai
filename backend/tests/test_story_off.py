"""Story mode switched off in the server config (STORY_MODE_ENABLED=0).

The API refuses story chats and every story route, and tells the frontend so
it can hide the mode. Ordinary chats are untouched.
"""

import pytest


@pytest.fixture(autouse=True)
def _story_off(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "STORY_MODE_ENABLED", False)


def test_me_reports_story_mode_off(signed_in):
    assert signed_in.get("/api/v1/auth/me").json()["storyModeEnabled"] is False


def test_story_chats_are_refused(signed_in):
    made = signed_in.post("/api/v1/chats", json={"mode": "story"})
    assert made.status_code == 403
    assert signed_in.post("/api/v1/chats", json={"mode": "chat"}).status_code == 200

    chat = signed_in.post("/api/v1/chats", json={"mode": "chat"}).json()
    streamed = signed_in.post(
        "/api/v1/chat/stream",
        json={"chatId": chat["id"], "messages": [{"role": "user", "content": "hi"}], "mode": "story"},
    )
    assert streamed.status_code == 403


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/v1/story/characters"),
        ("post", "/api/v1/story/characters"),
        ("patch", "/api/v1/story/characters/1"),
        ("delete", "/api/v1/story/characters/1"),
        ("get", "/api/v1/lore/characters"),
        ("get", "/api/v1/chats/x/story/chapters"),
        ("post", "/api/v1/chats/x/story/chapters"),
        ("patch", "/api/v1/chats/x/story/chapters/1"),
        ("delete", "/api/v1/chats/x/story/chapters/1"),
        ("post", "/api/v1/chats/x/story/chapters/1/resummarize"),
    ],
)
def test_story_routes_answer_404(signed_in, method, path):
    body = {"name": "Aria", "text": "note"} if method in ("post", "patch") else None
    response = getattr(signed_in, method)(path, **({"json": body} if body else {}))
    assert response.status_code == 404
    assert response.json()["detail"] == "Story mode is turned off"
