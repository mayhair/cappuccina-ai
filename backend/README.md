# Cappuccina Backend

FastAPI + LangGraph chatbot backend.

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env   # fill in OPENAI_API_KEY and BRAVE_API_KEY
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

Chat:

- `POST /api/v1/chat` — JSON `{chatId, messages, reasoning, mode, subject}` -> `{role, content, reasoning, tool_calls}`
- `POST /api/v1/chat/stream` — same body, SSE stream (`start`/`token`/`reasoning`/`tool_call`/`tool_result`/`done` events)

`reasoning`: `low|medium|high`. `mode`: `chat|tutor` (tutor takes `subject`).

## Structure

- `app/main.py` — FastAPI routes + CORS
- `app/schemas.py` — request/response models
- `app/config.py` — env + system prompts
- `app/auth.py` — session cookie + `current_user`
- `app/google_oauth.py` — OAuth code flow, token encryption, refresh
- `app/gmail.py` — Gmail REST client (search/read/draft/send) over httpx
- `app/db.py` — SQLite (users, sessions, chats, messages, google_grants, oauth_states)
- `agent/graph.py` — LangGraph: `call_model -> (tool?) -> tools -> call_model ... -> END`
- `agent/tools.py` — `solve_equation`, `brave_search`, `search_emails`, `read_email`, `draft_email`
- `agent/model.py` — model factory (`MODEL_ID` env, `-luna` default)

The authenticated user id reaches Gmail tools through LangGraph's
`config={"configurable": {"user_id": ...}}`; tools declare a `RunnableConfig`
parameter that is injected and hidden from the model's schema.

## Verify

```powershell
.\.venv\Scripts\python check_tools.py     # tool smoke test
.\.venv\Scripts\python -m pytest tests -q  # auth + Gmail unit tests
```
