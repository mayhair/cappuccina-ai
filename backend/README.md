# Cappuccina Backend

FastAPI + LangGraph chatbot backend.

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium   # headless browser for check_webpage
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
- `POST /api/v1/chat/stream` — same body, SSE stream (`start`/`token`/`reasoning`/`tool_call`/`tool_result`/`webpage`/`done` events)

Webpage environments (the canvas a built page renders in):

Every chat's site is a real folder on disk (`backend/data/sites/{site_id}/`) with
its own git history, driven through a container (`podman` by default; `docker`
works via `SITE_CONTAINER_CLI`); SQLite keeps only a registry row. Routes are
user-scoped except the preview.

- `GET /api/v1/webpages/{chat_id}` -> `{webpageId, files, updatedAt, activeVersionId,
  previewUrl, previewBase, entry, serveRoot, hasBuild, lastBuild}` (an empty shape
  before anything is written); `files` is just the file names and `lastBuild` is
  `{ok, error, at}` or null. `entry` is `index.html`, `serveRoot` is `dist` once a
  build exists else the workspace root, and `previewUrl`/`previewBase` are the
  served preview URL the canvas iframe loads and the base it rebases onto.
- `GET /api/v1/webpages/{chat_id}/files/{path}` — `{path, content}` for a text
  file, `{path, binary: true, mime, size}` otherwise; 404 when the file is
  missing, 400 for a name the write rules reject.
- `PUT /api/v1/webpages/{chat_id}/files` — JSON `{path, content, baseVersionId}`:
  save a hand-edited file as its own iteration (`baseVersionId` is the iteration
  the canvas rendered; a stale value is a 409). Same `validate_webpage_write` rules
  and messages as the `write_webpage_file` tool: a relative path of at most 10
  `[A-Za-z0-9._-]+` segments, no `..` or `.git*`, at most 1 MB per file, non-empty.
- `GET /api/v1/webpages/{chat_id}/versions` — `{activeVersionId, versions}`: the
  site's git history, one tagged commit per AI run or manual save, oldest first
  and capped at 50 (past the cap the oldest non-active iteration is retired). Each
  version is `{id, seq, parentVersionId, origin, label, messageUid, assets: [],
  summary: {files, added, removed}, createdAt}`.
- `PUT /api/v1/webpages/{chat_id}/active` — JSON `{versionId}`: check out that
  commit so later runs branch from it; 404 when it is not a live iteration.
- `GET /api/v1/webpages/{chat_id}/versions/{version_id}/diff[?against=]` — unified
  diff of one iteration against its parent (or an explicit base), read from git.
- `GET /api/v1/webpages/{chat_id}/preview/{key}/{path}` — the preview the canvas
  iframe loads, deliberately unauthenticated: the sandboxed canvas has an opaque
  origin, so module scripts and `fetch()` are CORS requests with `Origin: null`,
  and the `preview_key` in the URL is the capability. Serves `dist/` when it
  exists, else the workspace root, with SPA fallback to that root's `index.html`
  (and a placeholder page when there is none); sends `Access-Control-Allow-Origin:
  *`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` and
  `Cache-Control: no-cache`, and rebases root-absolute `href`/`src`/`action`/`url()`
  values in html/css onto the preview base. The build output (`npm run build`, when
  `package.json` declares a build script) is refreshed after each AI run and after
  manual saves and activations; a failed build surfaces in `lastBuild`.

Both `PUT`s refuse with 409 while that chat's reply is still streaming.

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
- `app/db.py` — SQLite (users, sessions, chats, messages, google_grants, oauth_states, images, attachments, webpage_sites). One shared connection whose statements are serialized under a lock, because sync tools run in worker threads (and one model message can carry several parallel tool calls): unsynchronized use made SQLite answer `InterfaceError: bad parameter or other API misuse`
- `app/sitebox.py` — container runtime for the webpage workspaces (env `SITE_CONTAINER_CLI`, `SITE_IDLE_MINUTES`, `SITE_EXEC_TIMEOUT`, `SITE_BUILD_TIMEOUT`): one container per site, idle stop + on-demand restart, command exec, `finalize_run`, build. The image tag is the Containerfile's content hash, so editing the image rebuilds it once (`podman image prune` drops the superseded tags); a container that already exists keeps the image and capabilities it was created with
- `app/webpage_git.py` — git history of a site folder: one tagged commit per AI run or manual save (50-tag cap), diffs from git
- `app/webpage_preview.py` — what the canvas preview serves for a path (dist/ or root, root-absolute URL rebasing, placeholder). Every html page gets the preview runtime injected first: an in-memory `localStorage`/`sessionStorage` when the sandboxed (opaque-origin) frame's real one throws, and a relay that posts uncaught errors to the canvas, which shows them with an "Ask Cappuccina to fix" button. The site's own `<script src>` tags are served `crossorigin` so those errors keep their real message. Files on disk are never changed
- `app/webpage_check.py` — headless Chromium (Python Playwright; `python -m playwright install chromium`) that loads a site in the canvas's own sandbox, from disk under a fake origin with only public https allowed out, then reports uncaught errors, console errors, missing files, `[hidden]` elements CSS keeps showing, and clicks every button (plus the ones a click reveals) to flag clicks that throw, reload the page or do nothing
- `sitebox/Containerfile` — the site image (`node:22-bookworm-slim` + git, curl, sudo) every site container runs. sudo's single rule allows `apt-get`/`apt`/`dpkg` (NOPASSWD) so the model can install a missing non-node package; `app/sitebox.py` grants the six capabilities that makes usable and drops everything else
- `app/tts.py` — Fish Audio TTS proxy: pending jobs + MP3 streaming
- `agent/graph.py` — LangGraph: `call_model -> (tool?) -> tools -> call_model ... -> END`; appends the FMHY ad-blocker safety note as a system message after FMHY tools run. When the model is about to finish a run that changed the site since its last check, `auto_check` runs `check_webpage` (shown as a tool card); a clean page ends the run, problems go back to the model as a system note so it fixes them (at most `AUTO_CHECK_LIMIT` rounds per run). A failing tool comes back to the model as an error `ToolMessage` (and a not-ok tool card) instead of ending the run — LangGraph's default handler would re-raise it and kill the SSE stream
- `agent/tools.py` — `solve_equation`, `web_search`, `read_webpage`, `lore_lookup`, `generate_image`/`image_edit` (accept a `characters` argument to attach lore reference looks), `read_attachment`, `write_webpage_file`/`read_webpage_file`/`add_webpage_asset` (build and edit the chat's container-backed website workspace; the asset tool copies a generated picture or a user attachment into the site folder), `webpage_shell` (run a bash command in the site's container; an unavailable runtime comes back as a plain message, never an exception), `check_webpage` (test the site in headless Chromium, optionally with scripted `steps`), `search_emails`, `read_email`, `draft_email`, `list_events`, `read_event`, `create_event`, `update_event`, `propose_event_deletion`
- `agent/fmhy.py` — FMHY free-media tools (`fmhy_media_sites`, `fmhy_search`, `fmhy_get_links`, `fmhy_get_section`, `fmhy_list_sections`) discovered over MCP at startup (the server cache is force-refreshed once at startup); filters NSFW-tagged entries and gates use until the user insists. `fmhy_media_sites` is the recommended path for "where can I watch X" — FMHY lists sites, not titles, so a title is looked up by reading the guide's full sections (Stream Aggregators, Download Games, Ebooks, …) rather than searching for it; results are starred-first and capped
- `agent/lorebook.py` — Cappuccina's lore book: `lore/characters/*.md` parsed into the system-prompt catalog + `lore_lookup` fuzzy name matching. An optional official look per character lives in `lore/characters/refs/{id}.{png,jpg,jpeg,webp}` (the assistant's own look is `refs/ballerina-cappuccina.*`) and is attached by the image tools (env `CHARACTER_REF_IMAGES`, on by default)
- `agent/model.py` — model factory (OpenRouter, `MODEL_ID` env, `glm-5.3-flash` default)

The authenticated user id reaches the Google tools through LangGraph's
`config={"configurable": {"user_id": ..., "chat_id": ..., "reply_id": ...}}`;
tools declare a `RunnableConfig` parameter that is injected and hidden from the
model's schema. `reply_id` is the assistant message the run is stored under, and
it is what folds every webpage write of one run into a single iteration when the
run finalizes (one tagged git commit).

## Verify

```powershell
.\.venv\Scripts\python check_tools.py     # tool smoke test
.\.venv\Scripts\python -m pytest tests -q  # auth + Gmail + webpage unit tests
```

`tests/test_webpage_preview.py` needs no container; `tests/test_sitebox.py` is
skipped unless a container engine answers (set `SITE_CONTAINER_CLI` to the CLI's
path when it is not on `PATH`).
