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

- `POST /api/v1/chat` — JSON `{chatId, messages, reasoning, mode, subject}` -> `{role, content}`
- `POST /api/v1/chat/stream` — same body, SSE stream (`start`/`token`/`done` events)

`reasoning`: `low|medium|high`. `mode`: `chat|tutor` (tutor takes `subject`).

## Structure

- `app/main.py` — FastAPI routes + CORS
- `app/schemas.py` — request/response models
- `app/config.py` — env + system prompts
- `agent/graph.py` — LangGraph: `call_model -> (tool?) -> tools -> call_model ... -> END`
- `agent/tools.py` — `solve_equation` (sympy), `brave_search` (Brave API)
- `agent/model.py` — model factory (`MODEL_ID` env, `-luna` default)

## Verify

```powershell
.\.venv\Scripts\python check_tools.py
```
