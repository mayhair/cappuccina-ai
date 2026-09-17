# Setup Guide — Cappuccina AI

How to install, configure, and run the app on **Windows** (PowerShell) and
**Unix-like** systems (macOS / Linux, bash/zsh).

## 1. What you're running

| Piece    | Tech                          | Default port |
|----------|-------------------------------|--------------|
| Frontend | Vite + React (`src/`)         | 5173 (dev) / 4173 (preview) |
| Backend  | FastAPI + LangGraph (`backend/`) | 8000      |

The Vite dev server proxies `/api` → `http://localhost:8000`, so the browser
only talks to the frontend port. Chat history lives in SQLite at
`backend/data/cappuccina.db` (auto-created, gitignored — delete it to reset).

## 2. Prerequisites

- **Python 3.11+** (the backend uses `X | Y` type syntax)
- **Node.js 20 LTS or newer** (Vite 5 requirement) + npm
- A terminal: **PowerShell 5.1+** on Windows, any POSIX shell on macOS/Linux
- API keys: an **OpenAI API key** (required) and a **Brave Search API key**
  (required for the web-search tool; chat still works without it)

Check yours:

```powershell
# Windows (PowerShell)
python --version
node --version
npm --version
```

```bash
# macOS / Linux
python3 --version
node --version
npm --version
```

## 3. Backend setup

From the repo root, create a virtualenv and install dependencies:

```powershell
# Windows (PowerShell) — run from the repo root
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

```bash
# macOS / Linux — run from the repo root
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> If Windows blocks the activation script, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then retry.

### Configure environment

Copy the example env file and fill in your keys:

```powershell
# Windows (PowerShell) — inside backend/
Copy-Item .env.example .env
notepad .env
```

```bash
# macOS / Linux — inside backend/
cp .env.example .env
nano .env
```

| Variable         | Required | Notes                                            |
|------------------|----------|--------------------------------------------------|
| `OPENAI_API_KEY` | Yes      | Chat + reasoning summaries                       |
| `BRAVE_API_KEY`  | For search | Web-search tool; chat works without it         |
| `MODEL_ID`       | No       | Defaults to `gpt-5.6-luna`                       |

`.env` is gitignored — never commit it.

## 4. Frontend setup

From the repo root:

```powershell
# Windows (PowerShell)
npm install
```

```bash
# macOS / Linux
npm install
```

## 5. Run it

### Option A — everything at once (recommended)

This starts the backend (with `--reload`) and the frontend together:

```powershell
# Windows (PowerShell), repo root
npm run dev:all
```

```bash
# macOS / Linux, repo root
npm run dev:all
```

Open **http://localhost:5173**. `Ctrl+C` stops both servers.
Ports can be overridden with `API_PORT` / `WEB_PORT` env vars, but note the
backend only allows CORS from `http://localhost:5173` and the Vite proxy
points at `:8000` — if you change ports, update `vite.config.js` /
`backend/app/main.py` to match.

### Option B — backend and frontend separately

Terminal 1 — backend (from `backend/`, venv activated):

```powershell
# Windows
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload
```

```bash
# macOS / Linux
python -m uvicorn app.main:app --port 8000 --reload
```

Terminal 2 — frontend (from the repo root):

```powershell
# Windows
npm run dev
```

```bash
# macOS / Linux
npm run dev
```

Same URL: **http://localhost:5173**. Drop `--reload` for a calmer backend log.

## 6. Verify it works

1. **Backend tools self-test** (venv activated, from `backend/`):

   ```powershell
   # Windows
   .\.venv\Scripts\python check_tools.py
   ```

   ```bash
   # macOS / Linux
   python check_tools.py
   ```

2. **In the browser:** sign up, send a message. A tool-using question
   (e.g. "weather in London") should show a *Thought for …* block with
   interleaved, collapsible tool calls.

3. **Automated UI checks** (Playwright, mocked backend — no API keys needed).
   Build, serve the preview, then run a check in a second terminal:

   ```powershell
   # Windows — terminal 1
   npm run build
   npm run preview -- --port 4173
   # Windows — terminal 2
   node scripts/tools-check.mjs
   ```

   ```bash
   # macOS / Linux — terminal 1
   npm run build
   npm run preview -- --port 4173
   # macOS / Linux — terminal 2
   node scripts/tools-check.mjs
   ```

   First time only, install the browser Playwright drives:

   ```bash
   npx playwright install chromium
   ```

   Other checks follow the same pattern: `scripts/flow-check.mjs`,
   `scripts/tutor-check.mjs`, `scripts/measure-font.mjs`,
   `scripts/emoji-check.mjs`, `scripts/capture.mjs`.

4. **CLI smoke test** (optional, needs a running backend + keys in `.env`):

   ```bash
   cd backend
   python chat_cli.py --reasoning high --mode tutor --subject fractions
   ```

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `fetch failed` / empty replies | Backend not running, or wrong port — frontend expects `:8000` |
| CORS errors in dev | You're not on `http://localhost:5173` — use that exact origin |
| `401` on chat send | Session expired — log out and back in |
| Search tool fails, chat is fine | Missing/invalid `BRAVE_API_KEY` in `backend/.env` |
| Model errors on startup | Missing `OPENAI_API_KEY`, or `MODEL_ID` your key can't access |
| Thinking blocks always say "Thinking..." | Stale DB from before durations were stored — fixed going forward; old rows show "Thought" |
| Weird local state | Delete `backend/data/cappuccina.db` to reset all chats (fresh `CREATE TABLE`s + migrations re-run on next start) |
| `pip install` fails on Windows | Use a matching Python 3.11+ from python.org; avoid the Store shim (`py -m pip install -r requirements.txt`) |
| Playwright `Executable doesn't exist` | Run `npx playwright install chromium` |

## 8. Repo map (for orientation)

- `src/` — React UI (`components/ChatWindow.jsx`, `hooks/useChatController.js`)
- `backend/app/` — FastAPI routes (`main.py`), schemas, SQLite (`db.py`), config
- `backend/agent/` — LangGraph (`graph.py`), model factory, tools
- `backend/data/` — SQLite file (created at runtime, ignored by git)
- `scripts/` — Playwright verification scripts + `dev-all.mjs` (dual runner)
- `assets/` — static images served as `/plates/...`
