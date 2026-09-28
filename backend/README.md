# Cappuccina Backend

FastAPI + LangGraph chatbot backend.

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env   # fill in OPENROUTER_API_KEY, OPENAI_API_KEY and PARALLEL_API_KEY
```

## Run

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Then `npm run dev` at the repo root — Vite proxies `/api` to :8000.

## Endpoints

Auth (session cookie `cappuccina_session`; sign-in is Google OAuth only):

- `GET /api/v1/auth/google/start` — 302 to Google's consent screen
- `GET /api/v1/auth/google/callback` — exchanges the code, links/creates the user, sets the session cookie
- `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`, `PATCH /api/v1/auth/me`

Gmail (incremental consent; tokens encrypted at rest):

- `GET /api/v1/gmail/connect` / `GET /api/v1/gmail/callback` — 302 flow for the Gmail scopes
- `GET /api/v1/gmail/status`, `POST /api/v1/gmail/disconnect`
- `PATCH /api/v1/gmail/drafts/{draft_id}` — user edits a draft before sending
- `POST /api/v1/gmail/drafts/{draft_id}/send` — the only path that actually sends mail

Google Calendar (incremental consent; per-service grant):

- `GET /api/v1/calendar/connect` / `GET /api/v1/calendar/callback` — 302 flow for `calendar.events`
- `GET /api/v1/calendar/status`, `POST /api/v1/calendar/disconnect`
- `POST /api/v1/calendar/events/{event_id}/delete` — the only path that deletes an event

Each connected Google service has its own grant row, so Gmail and Calendar can
be connected and disconnected independently.

Chat:

- `POST /api/v1/chat` — JSON `{chatId, messages, reasoning, mode, subject, timezone}` -> `{role, content, reasoning, tool_calls}`
- `POST /api/v1/chat/stream` — same body, SSE stream (`start`/`token`/`reasoning`/`tool_call`/`tool_result`/`done` events)

Text-to-speech (reads replies aloud; needs `FISH_API_KEY`, else 503):

- `POST /api/v1/tts` — JSON `{text}` -> `{id}` (registers one synthesis job)
- `GET /api/v1/tts/{id}` — streams the MP3 (`audio/mpeg`); fetchable once,
  only by the owning user. `app/tts.py` proxies Fish Audio, so the browser's
  `<audio>` element can start playing while the audio is still generating.

`reasoning`: `low|high|max`. `mode`: `chat|tutor` (tutor takes `subject`).
`timezone`: optional IANA name (from the browser). The current date is sent as
a system message after the chat history, rendered in that zone (falling back to
the connected calendar's zone, then UTC), so long chats always read the live date.

## Structure

- `app/main.py` — FastAPI routes + CORS
- `app/schemas.py` — request/response models
- `app/config.py` — env + system prompts
- `app/auth.py` — session cookie + `current_user`
- `app/google_oauth.py` — OAuth code flow, token encryption, refresh
- `app/gmail.py` — Gmail REST client (search/read/draft/send) over httpx
- `app/calendar.py` — Calendar REST client (list/read/create/update/delete) over httpx
- `app/db.py` — SQLite (users, sessions, chats, messages, google_grants, oauth_states)
- `app/tts.py` — Fish Audio TTS proxy: pending jobs + MP3 streaming
- `agent/graph.py` — LangGraph: `call_model -> (tool?) -> tools -> call_model ... -> END`; appends the FMHY ad-blocker safety note as a system message after FMHY tools run
- `agent/tools.py` — `solve_equation`, `web_search`, `read_webpage`, `lore_lookup`, `generate_image`/`image_edit` (accept a `characters` argument to attach lore reference looks), `read_attachment`, `search_emails`, `read_email`, `draft_email`, `list_events`, `read_event`, `create_event`, `update_event`, `propose_event_deletion`
- `agent/fmhy.py` — FMHY free-media tools (`fmhy_media_sites`, `fmhy_search`, `fmhy_get_links`, `fmhy_get_section`, `fmhy_list_sections`) discovered over MCP at startup (the server cache is force-refreshed once at startup); filters NSFW-tagged entries and gates use until the user insists. `fmhy_media_sites` is the recommended path for "where can I watch X" — FMHY lists sites, not titles, so a title is looked up by reading the guide's full sections (Stream Aggregators, Download Games, Ebooks, …) rather than searching for it; results are starred-first and capped
- `agent/lorebook.py` — Cappuccina's lore book: `lore/characters/*.md` parsed into the system-prompt catalog + `lore_lookup` fuzzy name matching. An optional official look per character lives in `lore/characters/refs/{id}.{png,jpg,jpeg,webp}` and is attached by the image tools (env `CHARACTER_REF_IMAGES`, on by default)
- `agent/model.py` — model factory (OpenRouter, `MODEL_ID` env, `glm-5.3-flash` default)

The authenticated user id reaches the Google tools through LangGraph's
`config={"configurable": {"user_id": ...}}`; tools declare a `RunnableConfig`
parameter that is injected and hidden from the model's schema.

## Verify

```powershell
.\.venv\Scripts\python check_tools.py     # tool smoke test
.\.venv\Scripts\python -m pytest tests -q  # auth + Gmail unit tests
```
