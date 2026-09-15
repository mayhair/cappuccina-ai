import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const API_PORT = process.env.API_PORT || "8000";
const WEB_PORT = process.env.WEB_PORT || "5173";

const venvPython = process.platform === "win32"
  ? path.join(root, "backend", ".venv", "Scripts", "python.exe")
  : path.join(root, "backend", ".venv", "bin", "python");
const python = existsSync(venvPython) ? venvPython : "python";
const vite = path.join(root, "node_modules", "vite", "bin", "vite.js");

function kill(proc) {
  if (!proc || proc.exitCode !== null || proc.signalCode) return;
  if (process.platform === "win32") {
    // /T kills the whole tree (uvicorn --reload spawns a child)
    spawn("taskkill", ["/pid", String(proc.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    try { process.kill(-proc.pid, "SIGTERM"); } catch { proc.kill("SIGTERM"); }
  }
}

let shuttingDown = false;
function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log("\nStopping backend...");
  kill(backend);
  kill(frontend);
  process.exit(code);
}

const backend = spawn(python, ["-m", "uvicorn", "app.main:app", "--port", API_PORT, "--reload"], {
  cwd: path.join(root, "backend"),
  stdio: "inherit",
  detached: process.platform !== "win32",
});
console.log(`Backend on :${API_PORT} (pid ${backend.pid})`);

const frontend = spawn(process.execPath, [vite, "--port", WEB_PORT], {
  cwd: root,
  stdio: "inherit",
  detached: process.platform !== "win32",
});
console.log(`Frontend on :${WEB_PORT} (proxies /api -> :${API_PORT}). Ctrl+C stops both.`);

backend.on("exit", (code) => { if (!shuttingDown) shutdown(code ?? 1); });
frontend.on("exit", (code) => { if (!shuttingDown) shutdown(code ?? 1); });
process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));
