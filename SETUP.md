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
- **Podman** (rootless) — each webpage canvas runs in its own site container
  (from the `node:22-bookworm-slim`-based image). Verified on 5.7 under Ubuntu
  26.04 (§7); on Windows, run `podman machine init && podman machine start`
  once. Docker Desktop works too: set `SITE_CONTAINER_CLI=docker` (see §3).
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
python -m playwright install chromium
```

```bash
# macOS / Linux — run from the repo root
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

`playwright install chromium` downloads the headless browser Cappuccina uses
to test the webpages she builds (the `check_webpage` tool and the automatic
check before she says a page is ready). Without it everything else works; the
check just reports that it isn't set up.

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
| `FMHY_MCP_URL`         | No         | FMHY free-media MCP endpoint; blank disables those tools     |
| `SITE_CONTAINER_CLI`   | Webpages   | Container CLI for the site sandboxes; defaults to `podman`  |
| `SITE_IDLE_MINUTES`    | No         | Stop a site's container after this many idle minutes (10)   |
| `SITE_EXEC_TIMEOUT`    | No         | Default `webpage_shell` timeout in seconds (120)            |
| `SITE_BUILD_TIMEOUT`   | No         | A site's `npm run build` timeout in seconds (300)           |

`.env` is gitignored — never commit it. Every variable with its default and a
one-line explanation is commented in `backend/.env.example`.

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

   Deploying to a server means adding the same three paths on the public origin
   you browse, with the same scheme as `APP_ORIGIN` (§7.5). Google's
   [redirect-URI rules](https://developers.google.com/identity/protocols/oauth2/web-server#uri-validation)
   prefer HTTPS for anything that isn't localhost — the live deployment
   registers plain `http://` and works, but use `https://` where you can.

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

### Option C — Linux VPS as a systemd service (rootless Podman)

That is a deployment, not a dev run: host preparation, the unit, the public
entry points and the update flow are all in **§7 — Deploy on a Linux VPS**. The
short version is that the backend runs as its own unprivileged user with
linger, subordinate ID ranges and delegated cgroup controllers, and rootless
Podman is driven by the app itself.

## 7. Deploy on a Linux VPS

The live deployment is three pieces — the first two run on the box, the third
is Cloudflare's:

| Piece | What it is |
|---|---|
| Backend (§3) | `uvicorn` on `127.0.0.1:8000`, started by a **system** systemd unit as an unprivileged user |
| Site containers | rootless Podman, same user, driven by the backend through the podman CLI |
| Public entry | a Cloudflare **Tunnel** for `/api/*` and the built frontend as a Cloudflare **Worker** |

Nothing on the host listens on a public port — the tunnel dials out. Worked
example throughout, from the live box: Ubuntu 26.04, deploy user `cappuccina`
(uid 1001), checkout `/srv/cappuccina`, public API `cappuccina-api.mayhair.net`,
public frontend `cappuccina.mayhair.net`.

### 7.1 Host preparation (as root)

The deploy user gets **no login shell and no sudo**: the app never needs host
root, and every container runs rootless inside that user's own namespace.

```bash
sudo useradd --create-home --shell /usr/sbin/nologin cappuccina
```

Rootless Podman needs its packages, subordinate ID ranges, a lingering user
manager, delegated cgroup controllers and unprivileged user namespaces:

```bash
sudo apt update
sudo apt install -y podman uidmap slirp4netns fuse-overlayfs crun dbus-user-session \
  python3-venv git curl
podman --version                                  # live box: 5.7.0
```

`python3-venv` is easy to miss on a minimal image — Ubuntu ships the venv/ensurepip
support in a versioned package (`python3.14-venv` on the live box), and without it
`python3 -m venv` fails.

Rootless Podman also needs subordinate ID ranges, a lingering user manager,
delegated cgroup controllers and unprivileged user namespaces — the user-level
half of the setup:

```bash
cat /etc/subuid /etc/subgid                       # pick a range nobody else holds
# live box: administrator has 100000:65536, so cappuccina got 165536:65536
sudo usermod --add-subuids 165536-231071 --add-subgids 165536-231071 cappuccina
grep '^cappuccina:' /etc/subuid /etc/subgid       # both lines must exist

sudo loginctl enable-linger cappuccina            # /run/user/<uid> and the user manager at boot
loginctl show-user cappuccina -p Linger           # Linger=yes

sudo mkdir -p /etc/systemd/system/user@.service.d
sudo tee /etc/systemd/system/user@.service.d/delegate.conf >/dev/null <<'EOF'
[Service]
Delegate=cpu cpuset io memory pids
EOF
sudo systemctl daemon-reload
sudo systemctl restart user@$(id -u cappuccina).service

sudo tee /etc/sysctl.d/99-podman-rootless.conf >/dev/null <<'EOF'
kernel.unprivileged_userns_clone = 1
kernel.apparmor_restrict_unprivileged_userns = 0
EOF
sudo sysctl --system                              # drop any key your kernel doesn't know

sudo ufw allow OpenSSH
```

`kernel.apparmor_restrict_unprivileged_userns=0` is the one Ubuntu 24.04+
addition: with AppArmor's userns restriction on, the user namespace is refused
and every `podman run` fails. Live box: that sysctl is `0`,
`user.max_user_namespaces` is `27245`.

Only SSH needs an inbound rule. Outbound is where the app lives, so leave the
default `allow outgoing`: the site image build pulls
`docker.io/library/node:22-bookworm-slim`, site containers reach the npm
registry, and the backend calls `openrouter.ai`, `api.openai.com`,
`api.parallel.ai`, `api.fish.audio`, Google's OAuth/Gmail/Calendar endpoints and
`fmhy-mcp.ken.tools` (blank `FMHY_MCP_URL` disables that tool).

Prove the environment before building on it. Two quirks of testing as this
user, both of which apply to every `sudo -u cappuccina` command in this
section: `sudo -u` inherits root's working directory (so `cd /` first, else
`cannot chdir to /home/…`), and the `nologin` shell rules out `sudo -i` and
`su -`.

```bash
cd / && sudo -u cappuccina env -i \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  HOME=/home/cappuccina \
  sh -c 'podman info | grep -E "rootless|cgroupVersion|cgroupManager" && podman run --rm docker.io/library/alpine echo hello'
```

Expect `rootless: true`, `cgroupVersion: v2`, `cgroupManager: systemd`, then
`hello`. `sudo -l -U cappuccina` should report that the user is **not allowed**
to run sudo — that is the intended state, not a problem to fix.

### 7.2 Checkout, venv, `.env`

The repo is **private**, so give the box its own read-only credential *before*
cloning — a deploy key, not a token in the remote URL:

```bash
cd / && sudo -u cappuccina sh -c 'ssh-keygen -q -t ed25519 -N "" -f ~/.ssh/id_ed25519 && ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts 2>/dev/null'
sudo -u cappuccina cat /home/cappuccina/.ssh/id_ed25519.pub   # GitHub → repo → Settings → Deploy keys → Add (read-only)
```

Then the checkout, venv and `.env`:

```bash
sudo install -d -o cappuccina -g cappuccina -m 750 /srv/cappuccina
cd / && sudo -u cappuccina git clone git@github.com:mayhair/cappuccina-ai.git /srv/cappuccina
cd / && sudo -u cappuccina python3 -m venv /srv/cappuccina/backend/.venv
cd / && sudo -u cappuccina /srv/cappuccina/backend/.venv/bin/pip install -r /srv/cappuccina/backend/requirements.txt
cd / && sudo /srv/cappuccina/backend/.venv/bin/python -m playwright install-deps chromium
cd / && sudo -u cappuccina /srv/cappuccina/backend/.venv/bin/python -m playwright install chromium
cd / && sudo -u cappuccina cp /srv/cappuccina/backend/.env.example /srv/cappuccina/backend/.env
```

A clone that fails with `Permission denied (publickey)` means the key was never
added to the repo; `Repository not found` over HTTPS means the same thing, since
GitHub answers `404` for private repos you can't read.

Fill `.env` exactly as in §3 — the API keys are the same keys — with the
production values:

| Variable | Value on a server |
|---|---|
| `APP_ORIGIN` | the origin **users browse**, scheme included (live: `http://cappuccina.mayhair.net`). It builds the three Google redirect URIs, and the session cookie gets its `Secure` flag only when it starts with `https://` — so a plain-`http` origin means a cookie that is also sent over http |
| `SITE_CONTAINER_CLI` | leave the default `podman` |
| `SITE_IDLE_MINUTES`, `SITE_*_TIMEOUT` | defaults are fine |

No CORS configuration is needed in production: the Worker serves the UI and
proxies `/api/*` to the tunnel, so the browser only ever talks to one origin
(the backend's CORS list is for localhost dev).

### 7.3 The systemd unit

`/etc/systemd/system/cappuccina.service`, with `cappuccina` and
`/srv/cappuccina/backend` replaced by your deployment user and checkout path:

```ini
[Unit]
Description=Cappuccina backend
After=network.target

[Service]
User=cappuccina
Group=cappuccina
WorkingDirectory=/srv/cappuccina/backend

Environment=PATH=/srv/cappuccina/backend/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

ExecStart=/srv/cappuccina/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Podman runs as the same user the service does (`User=`) — rootless Podman never
needs root here. The unit sets **no** `XDG_RUNTIME_DIR` and no
`DBUS_SESSION_BUS_ADDRESS`: Podman derives `/run/user/<uid>` from its own
effective UID, which is already the `User=` user. No systemd specifier can name
that UID — in a system unit `%U`, `%u`, `%h` and `%s` expand to the service
*manager's* values (`%U` becomes `0`, root), so setting
`Environment=XDG_RUNTIME_DIR=/run/user/%U` makes every Podman call fail with
`mkdir /run/user/0/libpod: permission denied`. For the same reason the unit does
not order itself after `user@%U.service` — that pulls in `user@0.service`; the
linger from §7.1 keeps the real runtime directory alive instead.

```bash
sudo tee /etc/systemd/system/cappuccina.service >/dev/null <<'UNIT'
[Unit]
Description=Cappuccina backend
After=network.target

[Service]
User=cappuccina
Group=cappuccina
WorkingDirectory=/srv/cappuccina/backend

Environment=PATH=/srv/cappuccina/backend/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

ExecStart=/srv/cappuccina/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now cappuccina.service
systemctl show cappuccina.service -p Environment -p ActiveState -p ExecMainStatus
journalctl -u cappuccina -f
```

Expected: `Environment=` holds only that `PATH` line, `ActiveState=active`,
`ExecMainStatus=0`, and no restart loop.

### 7.4 Public API — a Cloudflare Tunnel

There is no nginx, no caddy and no open HTTP port: `cloudflared` dials out to
Cloudflare, which terminates TLS for the API hostname.

1. Cloudflare dashboard → **Zero Trust → Networks → Tunnels → Create a tunnel
   → cloudflared**; name it (live: `cappuccina-api`) and copy the token.
2. Add a **public hostname**: `cappuccina-api.<your-domain>` → type `HTTP` →
   URL `127.0.0.1:8000`. The dashboard creates the DNS record itself.
3. On the box:

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared
sudo cloudflared service install <TOKEN-FROM-THE-DASHBOARD>
systemctl status cloudflared
```

That writes `/etc/cloudflared/token` (mode 600) and a unit whose `ExecStart` is
`/usr/bin/cloudflared --no-autoupdate tunnel run --token-file
/etc/cloudflared/token`. **The token wins**: ingress lives in the dashboard, so
a hand-written `/etc/cloudflared/config.yml` is ignored — don't maintain one.

Verify from anywhere:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://cappuccina-api.<your-domain>/api/v1/auth/me   # 401
```

### 7.5 Public frontend — the Worker

The UI is the built Vite app served by a Cloudflare Worker that proxies `/api/*`
to the tunnel hostname. This runs **from your machine**, not the box — Node and
npm are not installed on the server, and the backend serves no frontend assets
of its own:

```bash
npm install
npm run build
npx wrangler deploy
```

`wrangler` is not a dependency of this repo and there is no deploy script — `npx`
fetches it on demand. Authenticate once with `npx wrangler login` (browser
OAuth), or export `CLOUDFLARE_API_TOKEN` (and `CLOUDFLARE_ACCOUNT_ID` if the
token can see more than one account) for a non-interactive deploy.

`wrangler.jsonc` versions the parts that belong in the repo: worker name
`cappuccina-frontend`, `assets.directory: ./dist`, `run_worker_first:
["/api/*"]`, and `vars.API_ORIGIN` — point that at the tunnel hostname from
§7.4 (`https://cappuccina-api.<your-domain>`). Two things are **not** in the
repo and have to be set in the dashboard:

- **A route / custom domain for the worker.** `workers_dev` is `false` and no
  `routes` are declared, so a bare `wrangler deploy` publishes a worker with no
  reachable URL. Add `cappuccina.<your-domain>` under **Workers →
  cappuccina-frontend → Settings → Domains & Routes**.
- **The Google redirect URIs for the public origin**, in the same client as §4,
  matching `APP_ORIGIN` exactly — the live deployment registers the `http://`
  trio and signs in, but HTTPS is the better choice:

  ```
  https://cappuccina.<your-domain>/api/v1/auth/google/callback
  https://cappuccina.<your-domain>/api/v1/gmail/callback
  https://cappuccina.<your-domain>/api/v1/calendar/callback
  ```

Verify the worker is really proxying:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://cappuccina.<your-domain>/api/v1/auth/me   # 401
```

### 7.6 Updating a deployment

```bash
cd / && sudo -u cappuccina git -C /srv/cappuccina pull --ff-only
cd / && sudo -u cappuccina /srv/cappuccina/backend/.venv/bin/pip install -r /srv/cappuccina/backend/requirements.txt  # only if deps changed
cd / && sudo -u cappuccina /srv/cappuccina/backend/.venv/bin/python -m playwright install chromium  # after a playwright upgrade
sudo systemctl restart cappuccina.service
```

Frontend changes are `npm run build && npx wrangler deploy` from your machine.
Nothing else needs restarting: the site image is rebuilt by the app whenever the
Containerfile's hash changes.

### 7.7 What the app creates on first use

- `backend/data/cappuccina.db` (users, sessions, chats, messages, webpage
  registry) plus `data/attachments/`, `data/images/` and `data/sites/` — all
  gitignored, all created on demand.
- The site image, built lazily by the first `webpage_shell` call and tagged
  `cappuccina-site:<first 12 hex of the sha256 of backend/sitebox/Containerfile>`
  (`podman images cappuccina-site`). The build is capped at 600 s; on a slow box
  build it by hand with the same tag instead.
- One container per chat that owns a site, named `capweb-<site_id>`
  (`podman ps --filter name=capweb`), stopped after `SITE_IDLE_MINUTES` of
  inactivity and started again on the next command.

The webpage canvas needs this container runtime; every other feature runs
without it.

### 7.8 Acceptance test on the box

The site runtime has its own suite, and it is the one test that must pass on a
server (it needs a real engine, so it skips itself elsewhere). As the deploy
user, in the same environment the service runs in:

```bash
cd / && sudo -u cappuccina /srv/cappuccina/backend/.venv/bin/pip install -q -r /srv/cappuccina/backend/requirements-dev.txt
cd / && sudo -u cappuccina sh -c 'cd /srv/cappuccina/backend && .venv/bin/python -m pytest tests/test_sitebox.py -q -rs'
```

Expected on the live box: `8 passed, 1 skipped` — the skip is
`test_container_symlinks_are_dropped_so_git_can_stage`, which only applies to
Windows/WSL hosts. It covers engine availability, image build, create/start,
exec with exit codes, the timeout message, the npm build landing in `dist/`, the
in-container `curl` + restricted `apt-get` sudo, and idle stop/restart. It
removes its own container and volume.

Then prove the user-visible path end to end, in the app itself: open a chat that
owns a site and ask her to run something in the website terminal (e.g. *"in the
website terminal run `uname -a` and `ls`"*). The reply must carry a **Running
Site Command** card with `exit_code: 0` and the container's real output — never
*"The website workspace isn't available on this server right now"*. While it
runs:

```bash
cd / && sudo -u cappuccina podman ps --filter name=capweb
```

## 8. Verify it works

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
   `scripts/webpage-check.mjs` (webpage canvas: the preview iframe's `src` is the
   served preview URL — no inlining or `srcdoc` — the Code tab fetches file text
   one file at a time, Save sends `baseVersionId`, and "open in a new tab" loads
   the preview directly; reload restore),
   `scripts/webpage-versions-check.mjs` (iteration history, activation, Changes
   tab, in-canvas editing), `scripts/capture.mjs`.

4. **CLI smoke test** (optional, needs a running backend + keys in `.env`):

   ```bash
   cd backend
   python chat_cli.py --reasoning high --mode tutor --subject fractions
   ```

## 9. Troubleshooting

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
| Weird local state | Delete `backend/data/` to reset all chats and stored files (fresh `CREATE TABLE`s + migrations re-run on next start) |
| `pip install` fails on Windows | Use a matching Python 3.11+ from python.org; avoid the Store shim (`py -m pip install -r requirements.txt`) |
| Playwright `Executable doesn't exist` | Run `npx playwright install chromium` |
| **"The website workspace isn't available on this server right now"** | The runtime could not be reached *and* the build was silent about it. As of the current build the tool shows the runtime's own words instead, so check its message — a genuine connection loss keeps this line (`SITE_CONTAINER_CLI`, engine not running), anything else is quoted (§7.3) |
| `mkdir /run/user/0/libpod: permission denied` | The unit sets `XDG_RUNTIME_DIR=/run/user/%U`; in a **system** unit `%U` is the service manager's UID (`0`), not `User=`. Delete that line (and `DBUS_SESSION_BUS_ADDRESS`) — Podman derives the path itself (§7.3) |
| `podman run` fails at once on a fresh VPS | Check, in order: `sysctl kernel.apparmor_restrict_unprivileged_userns` is `0`, `loginctl show-user <user> -p Linger` is `Linger=yes`, `/etc/subuid` + `/etc/subgid` have the user's ranges, and the `user@.service` delegate drop-in is in place (§7.1) |
| `cannot chdir to /home/…` when testing as the deploy user | `sudo -u` inherits your working directory — `cd /` first (§7.1) |
| Cloudflare shows 502 for the API hostname | The tunnel is up but nothing answers `127.0.0.1:8000`: check `systemctl status cappuccina`, `journalctl -u cappuccina -n 50`, and that the tunnel's public hostname still points at that port (§7.4) |
| `wrangler deploy` succeeds but the site URL 404s | No custom domain/route on the worker — `workers_dev` is `false` in `wrangler.jsonc` (§7.5) |
| Sign-in works locally but not on the server | `APP_ORIGIN` must be the origin users browse, scheme included, and its three callback URIs must be registered in the Google client exactly as they are built (§7.5, §4) |
| Chat sends a site command and it hangs, then times out | The site container is starting or the image is building — the first call can take up to the build timeout (§7.7) |

## 10. Repo map (for orientation)

- `src/` — React UI (`components/ChatWindow.jsx`, `hooks/useChatController.js`)
- `backend/app/` — FastAPI routes (`main.py`), OAuth (`google_oauth.py`), Gmail
  client (`gmail.py`), schemas, SQLite (`db.py`), config
- `backend/agent/` — LangGraph (`graph.py`), model factory, tools
- `backend/tests/` — pytest suite (auth, linking, token refresh, Gmail)
- `backend/data/` — SQLite file + sharded `attachments/`, `images/` and
  `sites/` trees (all created at runtime, ignored by git)
- `scripts/` — Playwright verification scripts + `dev-all.mjs` (dual runner)
- `assets/` — static images served as `/plates/...`
