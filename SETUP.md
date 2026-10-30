# Setup Guide

This guide explains how to install, configure, and run Cappuccina AI. Section 1 through section 5 cover local development on Windows, macOS, and Linux. Section 6 covers production deployment on a Linux server.

## 1. Architecture

| Component | Stack | Default port |
|---|---|---|
| Frontend | Vite + React (`src/`) | 5173 (dev) / 4173 (preview) |
| Backend | FastAPI + LangGraph (`backend/`) | 8000 |

In local development, Vite proxies `/api` to `http://localhost:8000`, so the browser talks only to port `5173`. Chat history and accounts live in PostgreSQL (`DATABASE_URL`). The backend creates and updates the database schema automatically at startup. Uploaded attachments, generated images, and webpage workspaces stay on disk under `backend/data/`.

## 2. Prerequisites

Install these tools before you set up the project:

1. Install Python 3.11 or newer.
2. Install Node.js 20 LTS or newer and npm.
3. Install rootless Podman for the webpage containers, or use Docker Desktop and set `SITE_CONTAINER_CLI=docker` in `backend/.env`.
4. Install PostgreSQL 18 on a server, or run the local PostgreSQL 18 container from `compose.yaml` during development.
5. Prepare an OpenRouter API key, an OpenAI API key, a Parallel Search API key, and a Google OAuth client.

## 3. Backend setup

Create a Python virtual environment in `backend/` and install the Python packages. Then download headless Chromium for the `check_webpage` tool. Do not skip the Chromium step if you test webpage generation.

```powershell
# Windows (PowerShell) - run from the repo root
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

```bash
# macOS / Linux - run from the repo root
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

If Windows blocks `.venv\Scripts\Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and run the activation command again. On Linux, install the system libraries for Chromium once from `backend/`:

```bash
sudo .venv/bin/python -m playwright install-deps chromium
```

### Local PostgreSQL container

The `compose.yaml` file at the repository root runs `postgres:18` on `127.0.0.1:5432`. The user, password, and database name are `cappuccina`, and data persists in the `cappuccina-pg` volume. When `DATABASE_URL` is unset, the backend connects to `postgresql://cappuccina:cappuccina@127.0.0.1:5432/cappuccina` by default.

On Windows, install Podman Desktop and a compose provider (`docker-compose` or `pip install podman-compose`). Initialize a rootless Podman machine once. A rootful WSL machine does not forward container ports to Windows.

```powershell
# Windows (PowerShell) - one-time setup
podman machine init
podman machine start
podman machine inspect --format "{{.Rootful}}"   # must print: false
```

`npm run dev:all` starts the Podman machine and the `postgres` container automatically when `DATABASE_URL` points to `127.0.0.1` or `localhost`. You can also manage the database container manually from the repository root:

```bash
podman compose up -d                             # start the database
podman compose ps                                # status must reach "healthy"
podman compose exec postgres psql -U cappuccina  # open a SQL shell
podman compose down                              # stop and keep data
podman compose down -v                           # stop and delete all database data
```

### Environment configuration

Copy `backend/.env.example` to `backend/.env` and fill in your values. Never commit `backend/.env` to git. Every supported variable is documented in `backend/.env.example`.

```powershell
# Windows (PowerShell) - inside backend/
Copy-Item .env.example .env
```

```bash
# macOS / Linux - inside backend/
cp .env.example .env
```

| Variable | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | Chat model and reasoning via OpenRouter |
| `OPENAI_API_KEY` | For images | Image generation and editing tools |
| `PARALLEL_API_KEY` | For search | Web search tool |
| `FISH_API_KEY` | For voice | Fish Audio text-to-speech key (paused in code, see below) |
| `MODEL_ID` | No | Chat model ID (defaults to `z-ai/glm-5.3-flash`) |
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth web client ID (see section 4) |
| `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth web client secret (see section 4) |
| `TOKEN_ENC_KEY` | Yes | Key that encrypts Google tokens at rest (`openssl rand -hex 32`) |
| `GMAIL_ENABLED` | No | Set to `1` to enable Gmail tools (`0` by default) |
| `CALENDAR_ENABLED` | No | Set to `1` to enable Google Calendar tools (`0` by default) |
| `STORY_MODE_ENABLED` | No | Set to `0` to turn off Story mode (`1` by default) |
| `DATABASE_URL` | On servers | PostgreSQL URL (`postgresql:///cappuccina` on a Linux server) |
| `APP_ORIGIN` | No | Public URL that users open in the browser (`http://localhost:5173` by default) |
| `FMHY_MCP_URL` | No | FMHY MCP endpoint (leave empty to disable free-media tools) |
| `PASSWORD_AUTH_ENABLED` | No | Set to `0` to turn off email + password sign-in (`1` by default) |
| `TURNSTILE_SITE_KEY` | For email sign-in | Cloudflare Turnstile site key for the sign-up form (see section 6.6) |
| `TURNSTILE_SECRET_KEY` | For email sign-in | Turnstile secret key; when empty, sign-up skips the check |
| `MAIL_FROM` | For email sign-in | Sender of confirmation and reset emails, such as `Cappuccina <no-reply@example.com>` |
| `SMTP_HOST`, `SMTP_PORT` | No | Mail server for outgoing email (`127.0.0.1:25`, the local Postfix, by default) |
| `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_STARTTLS` | No | Login and STARTTLS, only for an SMTP relay |
| `SITE_CONTAINER_CLI` | For webpages | Container CLI for webpage workspaces (`podman` by default, or `docker`) |
| `SITE_IDLE_MINUTES` | No | Idle minutes before a webpage container stops (`10` by default) |
| `SITE_EXEC_TIMEOUT` | No | Timeout in seconds for `webpage_shell` (`120` by default) |
| `SITE_BUILD_TIMEOUT` | No | Timeout in seconds for `npm run build` in a site (`300` by default) |

Voice replies are off in code because the Fish Audio terms allow training on submitted text, which conflicts with Google rules for Gmail and Calendar data. To enable voice replies, use a voice provider that does not train on user text and update `legal/privacy-policy.md`. Then set `TTS_ENABLED = True` in `backend/app/config.py` and `VOICE_REPLIES_AVAILABLE = true` in `src/App.jsx`.

## 4. Google Cloud OAuth setup

Users can sign in with Google OAuth, or with email and password (section 6.6 covers the email side). The same OAuth client also handles the optional Gmail and Google Calendar connections. Set up the client in the [Google Cloud Console](https://console.cloud.google.com/):

1. Create a Google Cloud project.
2. If you use Gmail or Calendar, enable the Gmail API and the Google Calendar API in `APIs & Services -> Library`.
3. Open `APIs & Services -> OAuth consent screen` and choose External.
4. Fill in the app name and support email, and add every test account under Test users.
5. If you use Gmail or Calendar, add `.../auth/gmail.readonly`, `.../auth/gmail.compose`, `.../auth/gmail.send`, and `.../auth/calendar.events` to the scopes.
6. Open `APIs & Services -> Credentials` and create an OAuth client ID of type Web application.
7. Register the redirect URIs that match `APP_ORIGIN`:

```text
http://localhost:5173/api/v1/auth/google/callback
http://localhost:5173/api/v1/gmail/callback
http://localhost:5173/api/v1/calendar/callback
```

8. Copy the client ID and client secret into `backend/.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
9. Run `openssl rand -hex 32` and paste the output into `TOKEN_ENC_KEY` in `backend/.env`.

While `GMAIL_ENABLED` or `CALENDAR_ENABLED` is `0`, the backend deletes and revokes stored tokens for that service at startup. While the Google project is in Testing mode, only listed test users can sign in and refresh tokens expire after 7 days. When a token expires, reconnect the service in `Settings -> Connections`. The assistant only saves Gmail drafts and asks for user approval before deleting calendar events.

## 5. Frontend setup and local run

Install the frontend packages from the repository root, then start the development servers. `npm run dev:all` starts the local PostgreSQL container, the backend, and the frontend together.

```bash
npm install
npm run dev:all
```

Open `http://localhost:5173` in your browser. Press `Ctrl+C` to stop the frontend and backend servers.

You can also run the backend and frontend in separate terminals. Start the backend from `backend/` with the virtual environment active, and run `npm run dev` from the repository root:

```bash
# Terminal 1 (from backend/, with .venv active)
python -m uvicorn app.main:app --port 8000 --reload

# Terminal 2 (from the repo root)
npm run dev
```

## 6. Deploy on a Linux VPS

A production deployment has three parts. First, `uvicorn` listens on `127.0.0.1:8000` under a systemd unit as the unprivileged `cappuccina` user. Second, rootless Podman runs the webpage containers under the same `cappuccina` user. Third, a Cloudflare Tunnel exposes `/api/*` and a Cloudflare Worker serves the built frontend.

### 6.1 Host and rootless Podman setup

Create the `cappuccina` user without a login shell or sudo access. Install Podman and the dependencies for rootless containers and Python virtual environments.

```bash
sudo useradd --create-home --shell /usr/sbin/nologin cappuccina
sudo apt update
sudo apt install -y podman uidmap slirp4netns fuse-overlayfs crun dbus-user-session \
  python3-venv git curl
```

Configure subordinate UID and GID ranges, enable systemd linger for `cappuccina`, delegate cgroup v2 controllers, and allow unprivileged user namespaces:

```bash
cat /etc/subuid /etc/subgid   # choose an unused 65536-ID range
sudo usermod --add-subuids 165536-231071 --add-subgids 165536-231071 cappuccina

sudo loginctl enable-linger cappuccina

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
sudo sysctl --system

sudo ufw allow OpenSSH
```

Make sure that rootless Podman works as `cappuccina`. Run `cd /` before `sudo -u cappuccina` so the command does not fail trying to enter `/root`.

```bash
cd / && sudo -u cappuccina env -i \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  HOME=/home/cappuccina \
  sh -c 'podman info | grep -E "rootless|cgroupVersion|cgroupManager" && podman run --rm docker.io/library/alpine echo hello'
```

The output must show `rootless: true`, `cgroupVersion: v2`, `cgroupManager: systemd`, and `hello`.

### 6.2 PostgreSQL 18 setup

Install PostgreSQL 18 from the official PGDG repository so the server matches the local `postgres:18` image. Create the `cappuccina` role and the production and test databases.

```bash
sudo apt install -y postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt install -y postgresql-18

cd / && sudo -u postgres createuser --no-superuser --no-createdb --no-createrole cappuccina
cd / && sudo -u postgres createdb --owner=cappuccina cappuccina
cd / && sudo -u postgres createdb --owner=cappuccina cappuccina_test
```

PostgreSQL listens on `127.0.0.1:5432` and the local Unix socket with peer authentication. Do not open port `5432` in the firewall. Make sure that `cappuccina` can connect over the socket and that site containers cannot reach port `5432`:

```bash
cd / && sudo -u cappuccina psql -d cappuccina -c 'select version()'
cd / && sudo -u cappuccina env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/cappuccina \
  sh -c 'podman run --rm docker.io/library/alpine sh -c "nc -z -w 2 10.0.2.2 5432 && echo REACHABLE || echo blocked"'
```

The container command must print `blocked`.

### 6.3 Repository checkout and environment file

Clone the repository into `/srv/cappuccina` and create the Python virtual environment. Install the Python dependencies, install Chromium, and copy `backend/.env.example` to `backend/.env`. The repository is public, so the clone uses HTTPS and the server needs no SSH key or token. If you deploy your own fork, put its URL in the `git clone` command:

```bash
sudo install -d -o cappuccina -g cappuccina -m 750 /srv/cappuccina
cd / && sudo -u cappuccina git clone https://github.com/mayhair/cappuccina-ai.git /srv/cappuccina
cd / && sudo -u cappuccina python3 -m venv /srv/cappuccina/backend/.venv
cd / && sudo -u cappuccina /srv/cappuccina/backend/.venv/bin/pip install -r /srv/cappuccina/backend/requirements.txt
cd / && sudo /srv/cappuccina/backend/.venv/bin/python -m playwright install-deps chromium
cd / && sudo -u cappuccina /srv/cappuccina/backend/.venv/bin/python -m playwright install chromium
cd / && sudo -u cappuccina cp /srv/cappuccina/backend/.env.example /srv/cappuccina/backend/.env
```

Fill in the API keys and OAuth credentials in `/srv/cappuccina/backend/.env` as shown in section 3, and set these server values:

1. Set `DATABASE_URL=postgresql:///cappuccina` so the backend connects over the local Unix socket using peer authentication.
2. Set `APP_ORIGIN=https://cappuccina.<your-domain>` to match the exact URL and scheme that users open in the browser.

### 6.4 Systemd service

Create and start `/etc/systemd/system/cappuccina.service`. Do not set `XDG_RUNTIME_DIR` or `DBUS_SESSION_BUS_ADDRESS` in the unit file. Rootless Podman derives `/run/user/<uid>` from the `User=` account, and linger keeps that runtime directory active.

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
journalctl -u cappuccina -n 50
```

### 6.5 Cloudflare Tunnel and Frontend Worker

Expose the backend through an outbound Cloudflare Tunnel so no public HTTP port is open on the VPS:

1. In the Cloudflare dashboard, open `Zero Trust -> Networks -> Tunnels`, create a `cloudflared` tunnel, and copy the tunnel token.
2. Add a public hostname on the tunnel for `cappuccina-api.<your-domain>` pointing to `HTTP` at `127.0.0.1:8000`.
3. Install `cloudflared` on the VPS and register the service with your token:

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared
sudo cloudflared service install <TOKEN-FROM-THE-DASHBOARD>
```

Deploy the frontend from your local machine as a Cloudflare Worker that serves `./dist` and proxies `/api/*` to the tunnel hostname:

1. Make sure that `vars.API_ORIGIN` in `wrangler.jsonc` points to `https://cappuccina-api.<your-domain>`.
2. From the repository root on your local machine, run `npx wrangler login` once, then build and deploy the Worker:

```bash
npm install
npm run build
npx wrangler deploy
```

3. In the Cloudflare dashboard under `Workers -> cappuccina-frontend -> Settings -> Domains & Routes`, attach the custom domain `cappuccina.<your-domain>`.
4. Enable `SSL/TLS -> Edge Certificates -> Always Use HTTPS` in Cloudflare so browsers always send the `Secure` session cookie.
5. In the Google Cloud Console OAuth client from section 4, add the production redirect URIs matching `APP_ORIGIN`:

```text
https://cappuccina.<your-domain>/api/v1/auth/google/callback
https://cappuccina.<your-domain>/api/v1/gmail/callback
https://cappuccina.<your-domain>/api/v1/calendar/callback
```

### 6.6 Email sign-in: Turnstile and outgoing mail

Email + password sign-in needs two things on the server: Cloudflare Turnstile keys for the sign-up form, and a way to send the confirmation and password-reset emails. This guide sends them from a send-only Postfix on the VPS, so there is no mail provider to pay for. To use an SMTP relay instead, skip the Postfix steps and set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` and `SMTP_STARTTLS=1` in `backend/.env`.

#### Turnstile

1. In the Cloudflare dashboard, open `Turnstile`, add a widget in `Managed` mode, and add the hostname `cappuccina.<your-domain>`. Add `localhost` too if you want real keys in local development.
2. Put the keys in `/srv/cappuccina/backend/.env`:

```text
TURNSTILE_SITE_KEY=<site key>
TURNSTILE_SECRET_KEY=<secret key>
```

If `TURNSTILE_SECRET_KEY` is empty, sign-up works without the check and the backend logs a warning at startup. For local development, the Cloudflare test keys in `backend/.env.example` always pass.

#### Before you install Postfix

Mail from a server only reaches inboxes when the receiving servers trust the server's IP address. Check these first:

1. **Outbound port 25.** Many VPS hosts block it by default. Test it with `nc -vz -w 5 gmail-smtp-in.l.google.com 25`. If the connection times out, ask your host to unblock port 25, or use a relay as described above.
2. **Reverse DNS (PTR).** In your VPS host's panel, set the PTR record of the server's IPv4 address to `mail.<your-domain>`. Gmail and Outlook reject or flag mail from IPs without a matching PTR record. If the server also has IPv6, set its PTR as well, or make Postfix send over IPv4 only (step 2 below).
3. **IP reputation.** Look up the IP on a blocklist checker such as `https://mxtoolbox.com/blacklists.aspx`. If it is listed, ask your host for a different IP.

#### DNS records

In Cloudflare DNS for `<your-domain>`, add these records. Set each one to **DNS only** (grey cloud): mail does not go through the Cloudflare proxy.

| Type | Name | Content |
|---|---|---|
| `A` | `mail` | `<VPS IPv4 address>` |
| `TXT` | `@` | `v=spf1 ip4:<VPS IPv4 address> -all` |
| `TXT` | `_dmarc` | `v=DMARC1; p=quarantine; adkim=s; aspf=s; rua=mailto:<your address>` |

If the domain already has an SPF record (for example `include:_spf.mx.cloudflare.net` for Cloudflare Email Routing), add `ip4:<VPS IPv4 address>` to that record. A domain must have only one SPF record. The DKIM record comes in step 4 below.

#### Install and configure Postfix with DKIM

1. Install Postfix and OpenDKIM. When the installer asks, choose `Internet Site` and enter `<your-domain>` as the mail name:

```bash
sudo apt install -y postfix opendkim opendkim-tools
```

2. Make Postfix accept mail only from programs on this server, and send it from `mail.<your-domain>` over TLS:

```bash
sudo postconf -e 'myhostname = mail.<your-domain>'
sudo postconf -e 'myorigin = <your-domain>'
sudo postconf -e 'inet_interfaces = loopback-only'
sudo postconf -e 'inet_protocols = ipv4'
sudo postconf -e 'mydestination = localhost'
sudo postconf -e 'smtp_tls_security_level = may'
sudo postconf -e 'smtp_tls_loglevel = 1'
```

Leave out the `inet_protocols = ipv4` line if your IPv6 address has a PTR record and is listed in the SPF record.

3. Create a DKIM key. `cappuccina` is the selector name, and you can choose another one:

```bash
sudo install -d -o opendkim -g opendkim -m 700 /etc/opendkim/keys
sudo -u opendkim opendkim-genkey -b 2048 -d <your-domain> -s cappuccina -D /etc/opendkim/keys
sudo tee -a /etc/opendkim.conf >/dev/null <<'EOF'
Domain                  <your-domain>
Selector                cappuccina
KeyFile                 /etc/opendkim/keys/cappuccina.private
Socket                  inet:8891@127.0.0.1
Canonicalization        relaxed/simple
EOF
sudo postconf -e 'milter_default_action = accept'
sudo postconf -e 'smtpd_milters = inet:127.0.0.1:8891'
sudo postconf -e 'non_smtpd_milters = inet:127.0.0.1:8891'
sudo systemctl restart opendkim postfix
```

If `/etc/opendkim.conf` already has a `Socket` line, comment it out so that only the `inet:8891@127.0.0.1` line stays.

4. Publish the public key. Print it with `sudo cat /etc/opendkim/keys/cappuccina.txt`, join the quoted parts into one string, and add it in Cloudflare DNS as a `TXT` record named `cappuccina._domainkey` (DNS only). The content starts with `v=DKIM1; h=sha256; k=rsa; p=`.

5. Set the sender in `/srv/cappuccina/backend/.env`, then restart the backend:

```text
MAIL_FROM="Cappuccina <no-reply@<your-domain>>"
```

The `SMTP_*` defaults (`127.0.0.1:25`, no login) already point at this Postfix.

#### Check delivery

1. Send a test email with `printf 'Subject: test\n\nhello\n' | sendmail -f no-reply@<your-domain> <your Gmail address>`, and read the mail log with `sudo journalctl -u postfix -n 30`. Look for `status=sent`.
2. In Gmail, open the message, choose `Show original`, and check that SPF, DKIM and DMARC all say `PASS`.
3. Send one to the address that `https://www.mail-tester.com` shows you, and fix anything it reports.
4. Create an account on the site with email and password, and check that the confirmation email lands in the inbox and not in spam. Mail from a new IP can go to spam at first. Marking it `Not spam` helps, and so does a few days of normal sending.
5. Check that the rate limits see real visitor IPs, not one shared Cloudflare IP. Add a temporary log line for `client_ip(request)` in the login route in `backend/app/main.py`, sign in from two networks (for example Wi-Fi and mobile data), and read `journalctl -u cappuccina -n 50`. The Worker and the API share one Cloudflare zone, so `CF-Connecting-IP` carries the visitor's IP. If every sign-in shows the same IP, the per-IP limit acts as one global limit. The per-address limit still applies.

### 6.7 Updating the deployment

To update the backend on the VPS, pull the latest commits, update Python packages, and restart the service. At startup, the backend applies any new SQL files in `backend/app/migrations/` automatically.

```bash
cd / && sudo -u cappuccina git -C /srv/cappuccina pull --ff-only
cd / && sudo -u cappuccina /srv/cappuccina/backend/.venv/bin/pip install -q -r /srv/cappuccina/backend/requirements.txt
sudo systemctl restart cappuccina.service
```

If a release upgrades Playwright, also run `cd / && sudo -u cappuccina /srv/cappuccina/backend/.venv/bin/python -m playwright install chromium` before restarting. To update the frontend, run `npm run build && npx wrangler deploy` from your local machine.

To update the backend with one command, create `/usr/local/bin/deploy-cappuccina` on the VPS as root. `set -e` stops the script before the restart if `git pull` or `pip install` fails.

```bash
sudo tee /usr/local/bin/deploy-cappuccina >/dev/null <<'SCRIPT'
#!/bin/sh
set -e
cd /
sudo -u cappuccina git -C /srv/cappuccina pull --ff-only
sudo -u cappuccina /srv/cappuccina/backend/.venv/bin/pip install -q -r /srv/cappuccina/backend/requirements.txt
systemctl restart cappuccina.service
systemctl --no-pager status cappuccina.service | head -n 5
SCRIPT
sudo chown root:root /usr/local/bin/deploy-cappuccina
sudo chmod 755 /usr/local/bin/deploy-cappuccina
```

You can then deploy from your local machine with one SSH command:

```bash
ssh -t <your-vps> sudo deploy-cappuccina
```

To skip the `sudo` password prompt for that script, run `sudo visudo -f /etc/sudoers.d/deploy-cappuccina` on the VPS and add this rule. Make sure that `root` owns `/usr/local/bin/deploy-cappuccina` so no other user can edit it:

```text
<admin-user> ALL=(root) NOPASSWD: /usr/local/bin/deploy-cappuccina
```

### 6.8 Backups

User data lives in the `cappuccina` PostgreSQL database and on disk under `/srv/cappuccina/backend/data/`. Back up both every night and delete backups older than 30 days to match the retention rule in `legal/privacy-policy.md`.

```bash
sudo install -d -o cappuccina -g cappuccina -m 700 /var/backups/cappuccina

sudo tee /etc/systemd/system/cappuccina-backup.service >/dev/null <<'UNIT'
[Unit]
Description=Cappuccina nightly backup (database + data directory)

[Service]
Type=oneshot
User=cappuccina
Group=cappuccina
UMask=0077
WorkingDirectory=/srv/cappuccina/backend
ExecStart=/bin/sh -c 'set -e; d=/var/backups/cappuccina; s=$$(date +%%F); \
  pg_dump -Fc -d cappuccina -f $$d/db-$$s.dump; \
  tar -czf $$d/data-$$s.tar.gz -C /srv/cappuccina/backend data; \
  find $$d -type f \\( -name "db-*.dump" -o -name "data-*.tar.gz" \\) -mtime +29 -delete'
UNIT

sudo tee /etc/systemd/system/cappuccina-backup.timer >/dev/null <<'UNIT'
[Unit]
Description=Nightly Cappuccina backup

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now cappuccina-backup.timer
sudo systemctl start cappuccina-backup.service
```

To restore a backup on the server, stop `cappuccina.service` and recreate the `cappuccina` database. Run `pg_restore -d cappuccina --no-owner /var/backups/cappuccina/db-<date>.dump` as `cappuccina`, extract `data-<date>.tar.gz` into `/srv/cappuccina/backend`, and start `cappuccina.service`. If you copy backups off the server, apply the same 30-day retention rule to the remote storage.

## 7. Testing

Run the backend tool self-test and the unit test suite from `backend/` with the virtual environment active. The unit tests use `TEST_DATABASE_URL` (which defaults to `postgresql://cappuccina:cappuccina@127.0.0.1:5432/cappuccina_test`) and never modify your development database.

```bash
pip install -r requirements-dev.txt
python check_tools.py
python -m pytest tests -q
```

On the Linux VPS, run the container sandbox test suite against `cappuccina_test`:

```bash
cd / && sudo -u cappuccina sh -c 'cd /srv/cappuccina/backend && TEST_DATABASE_URL=postgresql:///cappuccina_test .venv/bin/python -m pytest tests/test_sitebox.py -q -rs'
```

To run the automated Playwright UI tests locally (which use a mocked backend and need no API keys), install the Playwright browser once with `npx playwright install chromium`. Then build and preview the frontend in one terminal and run any test script from `scripts/` in a second terminal:

```bash
# Terminal 1
npm run build
npm run preview -- --port 4173

# Terminal 2
node scripts/tools-check.mjs
```

## 8. Troubleshooting

Look up common errors and their fixes in the table below. Each row lists the symptom and the action that resolves it. If a backend error persists, read `journalctl -u cappuccina -n 50` on the server.

| Symptom | Cause and fix |
|---|---|
| `fetch failed` or empty replies | The backend is not running on port `8000`. Start the backend and try again. |
| CORS errors in local development | Open `http://localhost:5173` instead of another port or hostname. |
| `connection refused` on `127.0.0.1:5432` | Locally, run `podman machine start` and `podman compose up -d`. On a server, inspect `systemctl status postgresql` and `DATABASE_URL` in `backend/.env`. |
| Connection refused on Windows when `postgres` is `healthy` | The Podman machine is rootful. Run `podman machine stop`, `podman machine set --rootful=false`, `podman machine start`, and `podman compose up -d`. |
| On Windows, `podman machine start` prints `win-sshproxy.exe failed to start`, and `podman compose up -d` cannot find the `podman-machine-default` pipe | An old socket file from a Podman session that did not stop cleanly blocks the Docker API forwarding. Run `podman machine stop`, delete `%LOCALAPPDATA%\Temp\podman\podman-machine-default-api.sock`, run `podman machine start`, and then run `podman compose up -d`. Podman makes a new socket file each time it starts. |
| `Peer authentication failed` on the VPS | Make sure that the service runs as the OS user `cappuccina` and that the PostgreSQL role `cappuccina` exists. |
| `redirect_uri_mismatch` during Google sign-in | Make sure that the redirect URIs in Google Cloud Console match `APP_ORIGIN` in `backend/.env`, including `http://` or `https://`. |
| Sign-in finishes at Google but you stay logged out | `APP_ORIGIN` uses `https://` (so the cookie is `Secure`), but you opened `http://`. Enable Always Use HTTPS in Cloudflare. |
| `Access blocked` during Google sign-in | Add your Google account under Test users on the OAuth consent screen. |
| Sign-up says `We couldn't send the email` | The backend cannot reach the mail server. Run `systemctl status postfix` and check `SMTP_HOST` and `SMTP_PORT`. Locally, start a test mail server: `python -m aiosmtpd -n -l 127.0.0.1:1025` with `SMTP_PORT=1025`. |
| Confirmation emails go to spam or never arrive | Check the PTR record, the SPF, DKIM and DMARC records, and `sudo journalctl -u postfix` (see section 6.6). |
| The security check never shows on the sign-up form | `TURNSTILE_SITE_KEY` is wrong, or the widget's hostnames in Cloudflare don't include the site's hostname. |
| `Testing webpages isn't set up on this server` | Run `python -m playwright install chromium` in `backend/.venv`, and on Linux run `sudo .venv/bin/python -m playwright install-deps chromium`. |
| `podman run` fails on a fresh VPS | Make sure that `kernel.apparmor_restrict_unprivileged_userns` is `0`, `Linger=yes` is set for `cappuccina`, `/etc/subuid` and `/etc/subgid` contain the user range, and `delegate.conf` is loaded. |
| `wrangler deploy` succeeds but the site returns `404` | Attach your custom domain under `Workers -> cappuccina-frontend -> Settings -> Domains & Routes` in Cloudflare. |
| Corrupted local state | Run `podman compose down -v && podman compose up -d` and delete `backend/data/` to reset local data. |

## 9. Repository layout

The project is split between the React frontend in `src/`, the FastAPI and LangGraph backend in `backend/`, and the Cloudflare Worker in `worker/`. Runtime files live in `backend/data/` and are ignored by git. The numbered list below describes each top-level path:

1. `src/` contains the React frontend (`components/ChatWindow.jsx`, `hooks/useChatController.js`, `components/WebpageCanvas.jsx`, `components/MemoryManager.jsx`).
2. `backend/app/` contains FastAPI routes (`main.py`), Google OAuth (`google_oauth.py`), Gmail (`gmail.py`), Calendar (`calendar.py`), PostgreSQL pool and schema migrations (`db.py`, `migrations/`), and webpage containers (`sitebox.py`).
3. `backend/agent/` contains the LangGraph agent (`graph.py`), model factory (`model.py`), tools (`tools.py`), lorebook (`lorebook.py`), and memory rules (`memory.py`).
4. `backend/tests/` contains the pytest test suite.
5. `backend/data/` holds runtime `attachments/`, `images/`, and `sites/` directories (gitignored).
6. `compose.yaml` defines the local PostgreSQL 18 container for development.
7. `scripts/` contains the `dev-all.mjs` runner and Playwright UI test scripts.
8. `worker/` and `wrangler.jsonc` define the Cloudflare Worker that serves the production frontend and proxies `/api/*`.
