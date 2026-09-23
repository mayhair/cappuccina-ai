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
- API keys: an **OpenRouter API key** (required, chat model), an **OpenAI API
  key** (required, image generation), a **Parallel Search API key**
  (required for the web-search tool; chat still works without it), and a
  **Google OAuth client** (required to sign in — see §4)

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

| Variable               | Required   | Notes                                                        |
|------------------------|------------|--------------------------------------------------------------|
| `OPENROUTER_API_KEY`   | Yes        | Chat model + reasoning (via OpenRouter)                      |
| `OPENAI_API_KEY`       | For images | Image generation tool; chat works without it                 |
| `PARALLEL_API_KEY`     | For search | Web-search tool; chat works without it                       |
| `FISH_API_KEY`         | For voice  | Fish Audio key; reads replies aloud (speaker icon); optional |
| `MODEL_ID`             | No         | Defaults to `z-ai/glm-5.3-flash` (OpenRouter)                |
| `GOOGLE_CLIENT_ID`     | Yes        | Google OAuth web client (see §4)                             |
| `GOOGLE_CLIENT_SECRET` | Yes        | Google OAuth web client (see §4)                             |
| `TOKEN_ENC_KEY`        | Yes        | Encrypts Gmail tokens at rest; `openssl rand -hex 32`        |
| `APP_ORIGIN`           | No         | Origin users browse; defaults to `http://localhost:5173`     |

`.env` is gitignored — never commit it.

## 4. Google Cloud setup (sign-in + Gmail)

Sign-in is Google-only, and the same OAuth client is used for the optional
Gmail connection. In [Google Cloud Console](https://console.cloud.google.com/):

1. **Create a project** (e.g. "Cappuccina").
2. **Enable the Gmail and Google Calendar APIs**: *APIs & Services → Library*,
   then enable **Gmail API** and **Google Calendar API**.
3. **Configure the consent screen**: *APIs & Services → OAuth consent screen*.
   - User type: **External**, then fill in app name and support email.
   - Scopes: add `.../auth/gmail.readonly`, `.../auth/gmail.compose`,
     `.../auth/gmail.send`, and `.../auth/calendar.events`.
   - **Test users**: add every Google account that will try the app. While the
     app is in *Testing*, only these accounts can sign in.
4. **Create credentials**: *APIs & Services → Credentials → Create
   credentials → OAuth client ID → Web application*. Under **Authorized
   redirect URIs** add all three (must match `APP_ORIGIN` exactly):

   ```
   http://localhost:5173/api/v1/auth/google/callback
   http://localhost:5173/api/v1/gmail/callback
   http://localhost:5173/api/v1/calendar/callback
   ```

5. Copy the **client ID** and **client secret** into `backend/.env` as
   `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, and generate the token key:

   ```bash
   openssl rand -hex 32   # paste into TOKEN_ENC_KEY
   ```

Notes:

- The Gmail scopes are **restricted scopes**; `calendar.events` is a
  **sensitive scope**. While the app stays in *Testing* mode this is fine for a
  handful of accounts (up to 100 test users), but Google also expires refresh
  tokens after 7 days in Testing — users reconnect in **Settings →
  Connections** when that happens. Serving the public needs OAuth verification
  (and, for Gmail, a CASA security assessment).
- The app never sends email on its own. Gmail tools only search/read and save
  **drafts**; the user presses Send in the chat.
- The app never deletes a calendar event on its own either. It adds and edits
  events directly, but deletions are shown as a card the user confirms.

## 5. Frontend setup

From the repo root:

```powershell
# Windows (PowerShell)
npm install
```

```bash
# macOS / Linux
npm install
```

## 6. Run it

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

## 7. Verify it works

1. **Backend tools self-test** (venv activated, from `backend/`):

   ```powershell
   # Windows
   .\.venv\Scripts\python check_tools.py
   ```

   ```bash
   # macOS / Linux
   python check_tools.py
   ```

   **Backend unit tests** (auth, account linking, token refresh, Gmail
   endpoints; no network needed):

   ```bash
   # from backend/
   .\.venv\Scripts\python -m pytest tests -q   # Windows
   .venv/bin/python -m pytest tests -q         # macOS / Linux
   ```

   Install dev deps first if needed: `pip install -r requirements-dev.txt`.

2. **In the browser:** sign in with Google, send a message. A tool-using
   question (e.g. "weather in London") should show a *Thought for …* block with
   interleaved, collapsible tool calls. Open **Settings → Connections → Connect
   Gmail**, then ask her to check your inbox — she saves drafts you send
   yourself.

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
   `scripts/tutor-check.mjs`, `scripts/draft-check.mjs` (email draft → Send
   button), `scripts/measure-font.mjs`, `scripts/emoji-check.mjs`,
   `scripts/capture.mjs`.

4. **CLI smoke test** (optional, needs a running backend + keys in `.env`):

   ```bash
   cd backend
   python chat_cli.py --reasoning high --mode tutor --subject fractions
   ```

## 8. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `fetch failed` / empty replies | Backend not running, or wrong port — frontend expects `:8000` |
| CORS errors in dev | You're not on `http://localhost:5173` — use that exact origin |
| `401` on chat send | Session expired — log out and back in |
| Sign-in says Google isn't set up | Missing `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` in `backend/.env` |
| `redirect_uri_mismatch` from Google | The redirect URIs in Console must match `APP_ORIGIN` exactly (`http://localhost:5173/api/v1/...`) |
| "Access blocked" during sign-in | Your Google account isn't in **Test users** on the consent screen |
| Gmail connect instantly errors | Consent-screen scopes missing, or `TOKEN_ENC_KEY` empty |
| Gmail disconnects every ~7 days | Testing-mode refresh tokens expire weekly — reconnect in Settings (see §4) |
| Search tool fails, chat is fine | Missing/invalid `PARALLEL_API_KEY` in `backend/.env` |
| Model errors on startup | Missing `OPENROUTER_API_KEY`, or `MODEL_ID` your key can't access |
| Thinking blocks always say "Thinking..." | Stale DB from before durations were stored — fixed going forward; old rows show "Thought" |
| Weird local state | Delete `backend/data/cappuccina.db` to reset all chats (fresh `CREATE TABLE`s + migrations re-run on next start) |
| `pip install` fails on Windows | Use a matching Python 3.11+ from python.org; avoid the Store shim (`py -m pip install -r requirements.txt`) |
| Playwright `Executable doesn't exist` | Run `npx playwright install chromium` |

## 9. Repo map (for orientation)

- `src/` — React UI (`components/ChatWindow.jsx`, `hooks/useChatController.js`)
- `backend/app/` — FastAPI routes (`main.py`), OAuth (`google_oauth.py`), Gmail
  client (`gmail.py`), schemas, SQLite (`db.py`), config
- `backend/agent/` — LangGraph (`graph.py`), model factory, tools
- `backend/tests/` — pytest suite (auth, linking, token refresh, Gmail)
- `backend/data/` — SQLite file (created at runtime, ignored by git)
- `scripts/` — Playwright verification scripts + `dev-all.mjs` (dual runner)
- `assets/` — static images served as `/plates/...`
