import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const API_PORT = process.env.API_PORT || "8000";
const WEB_PORT = process.env.WEB_PORT || "5173";
const DEFAULT_DATABASE_URL = "postgresql://cappuccina:cappuccina@127.0.0.1:5432/cappuccina";

// DATABASE_URL as the backend will see it: the environment wins over backend/.env.
function databaseUrl() {
  if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
  const envFile = path.join(root, "backend", ".env");
  if (existsSync(envFile)) {
    const m = readFileSync(envFile, "utf8").match(/^\s*DATABASE_URL\s*=\s*["']?([^"'\r\n#]*)/m);
    if (m && m[1].trim()) return m[1].trim();
  }
  return DEFAULT_DATABASE_URL;
}

function portOpen(host, port, timeoutMs = 1000) {
  return new Promise((resolve) => {
    const sock = net.connect({ host, port });
    const done = (ok) => { sock.destroy(); resolve(ok); };
    sock.setTimeout(timeoutMs, () => done(false));
    sock.once("connect", () => done(true));
    sock.once("error", () => done(false));
  });
}

function run(cli, args, opts = {}) {
  return spawnSync(cli, args, { cwd: root, encoding: "utf8", ...opts });
}

// Starts the compose.yaml postgres container when DATABASE_URL points at it and
// nothing is listening yet. A remote or socket DATABASE_URL is left alone.
async function ensurePostgres() {
  let url;
  try { url = new URL(databaseUrl()); } catch { return; }
  const host = url.hostname.replace(/^\[|\]$/g, "");
  if (!["127.0.0.1", "localhost", "::1"].includes(host)) return;
  const port = Number(url.port || 5432);
  if (await portOpen("127.0.0.1", port)) return;

  const cli = process.env.SITE_CONTAINER_CLI || "podman";
  const isPodman = path.basename(cli).toLowerCase().startsWith("podman");
  if (run(cli, ["--version"]).error) {
    console.warn(`PostgreSQL is not running on :${port} and '${cli}' was not found; start it yourself.`);
    return;
  }

  // On Windows/macOS Podman runs containers inside a VM that must be up first.
  if (isPodman && process.platform !== "linux") {
    const state = run(cli, ["machine", "inspect", "--format", "{{.State}}"]).stdout?.trim();
    if (state !== "running") {
      console.log("Starting Podman machine...");
      run(cli, ["machine", "start"], { stdio: "inherit" });
    }
  }

  // Restart the existing compose container directly: plain `podman start` works
  // even when the external compose provider can't reach the machine's API pipe.
  const findContainer = () => run(cli, [
    "ps", "-a", "--format", "{{.Names}}",
    "--filter", `label=com.docker.compose.project=${path.basename(root).toLowerCase()}`,
    "--filter", "label=com.docker.compose.service=postgres",
  ]).stdout?.trim().split(/\r?\n/)[0];
  let container = findContainer();
  console.log("Starting PostgreSQL container...");
  if (container) {
    if (run(cli, ["start", container], { stdio: "inherit" }).status !== 0) container = undefined;
  }
  if (!container) {
    const up = run(cli, ["compose", "up", "-d", "postgres"], { stdio: "inherit" });
    container = up.status === 0 && findContainer();
    if (!container) {
      console.warn(`'${cli} compose up -d postgres' failed; the backend may not reach the database.`);
      return;
    }
  }

  // Same check as the compose healthcheck, so the backend's startup migrations don't race it.
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const ready = run(cli, ["exec", container, "pg_isready", "-U", "cappuccina", "-d", "cappuccina"]);
    if (ready.status === 0 && await portOpen("127.0.0.1", port)) {
      console.log(`PostgreSQL ready on :${port}`);
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  console.warn("PostgreSQL did not become ready within 60s; starting anyway.");
}

await ensurePostgres();

const venvPython = process.platform === "win32"
  ? path.join(root, "backend", ".venv", "Scripts", "python.exe")
  : path.join(root, "backend", ".venv", "bin", "python");
const python = existsSync(venvPython) ? venvPython : "python";
const vite = path.join(root, "node_modules", "vite", "bin", "vite.js");

const running = (proc) => proc && proc.exitCode === null && !proc.signalCode;

function exited(proc) {
  return running(proc) ? new Promise((r) => proc.once("exit", r)) : Promise.resolve();
}

function kill(proc, force) {
  if (!running(proc)) return Promise.resolve();
  if (process.platform === "win32") {
    // /T kills the whole tree (uvicorn --reload spawns a child). Without /F,
    // taskkill can't stop console apps, so Windows only has a forced kill.
    return new Promise((r) => {
      spawn("taskkill", ["/pid", String(proc.pid), "/T", "/F"], { stdio: "ignore" }).once("close", r);
    });
  }
  const sig = force ? "SIGKILL" : "SIGTERM";
  try { process.kill(-proc.pid, sig); } catch { proc.kill(sig); }
  return Promise.resolve();
}

const withTimeout = (promise, ms) => Promise.race([promise, new Promise((r) => setTimeout(r, ms))]);

// Waits for the children to exit before exiting, so their shutdown logs don't
// spill out after the shell prompt has come back.
let shuttingDown = false;
async function shutdown(code, { signal = false } = {}) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log("\nStopping...");
  // npm has already given the prompt back (see relayOutput), so stay quiet.
  if (signal && relayOutput) muted = true;
  const children = [backend, frontend];
  // On Windows, Ctrl+C already reached the children (same console), so let them
  // stop on their own. Everywhere else, ask them to stop.
  if (!(signal && process.platform === "win32")) {
    await Promise.all(children.map((p) => kill(p, process.platform === "win32")));
  }
  await withTimeout(Promise.all(children.map(exited)), 5000);
  await Promise.all(children.map((p) => kill(p, true)));
  await withTimeout(Promise.all(children.map(exited)), 2000);
  if (!muted) console.log("Stopped.");
  process.exit(code);
}

// On Windows, npm answers Ctrl+C by killing its script shell outright and
// returns to the prompt while we are still shutting down. Under npm there we
// relay the children's output ourselves, so it can be muted after Ctrl+C
// instead of spilling over the prompt.
const relayOutput = process.platform === "win32" && !!process.env.npm_lifecycle_event;
let muted = false;

function startChild(command, args, { cwd, env }) {
  const proc = spawn(command, args, {
    cwd,
    env: { ...process.env, ...(relayOutput ? env : {}) },
    stdio: relayOutput ? ["inherit", "pipe", "pipe"] : "inherit",
    detached: process.platform !== "win32",
  });
  if (relayOutput) {
    proc.stdout.on("data", (chunk) => { if (!muted) process.stdout.write(chunk); });
    proc.stderr.on("data", (chunk) => { if (!muted) process.stderr.write(chunk); });
  }
  return proc;
}

const backend = startChild(python, [
  "-m", "uvicorn", "app.main:app", "--port", API_PORT, "--reload",
  ...(relayOutput ? ["--use-colors"] : []),
], {
  cwd: path.join(root, "backend"),
  env: { PYTHONUNBUFFERED: "1" }, // piped Python output is block-buffered otherwise
});
console.log(`Backend on :${API_PORT} (pid ${backend.pid}), waiting for startup...`);

let frontend;
backend.on("exit", (code) => { if (!shuttingDown) shutdown(code ?? 1); });
process.on("SIGINT", () => shutdown(0, { signal: true }));
process.on("SIGTERM", () => shutdown(0, { signal: true }));

// Any HTTP response means startup is complete: with --reload the socket accepts
// connections early, but requests are only answered once the app has started.
async function waitForBackend(timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline && !shuttingDown) {
    try {
      await fetch(`http://127.0.0.1:${API_PORT}/`, { signal: AbortSignal.timeout(2000) });
      return true;
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  return false;
}

if (!(await waitForBackend())) {
  if (shuttingDown) await new Promise(() => {}); // shutdown() is exiting
  console.warn("Backend did not answer within 120s; starting the frontend anyway.");
}

frontend = startChild(process.execPath, [vite, "--port", WEB_PORT], {
  cwd: root,
  env: { FORCE_COLOR: "1" },
});
console.log(`Frontend on :${WEB_PORT} (proxies /api -> :${API_PORT}). Ctrl+C stops both.`);
frontend.on("exit", (code) => { if (!shuttingDown) shutdown(code ?? 1); });
