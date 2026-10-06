"""Containerized website workspaces (rootless Podman, Docker-compatible).

Every chat's webpage gets one long-lived container named `capweb-{site_id}`
running the `cappuccina-site` image (tagged with the Containerfile's content
hash, so editing the image rebuilds it once instead of reusing a stale one).
The container's /workspace is the host
workspace directory bind-mounted read-write, so preview, history and diffs can
read dist/ and .git/ straight off the host with the container stopped — the
container is only needed to run commands. A named volume covers
/workspace/node_modules so `npm install` never writes through the Windows
bind mount, which is the slow path.

There is deliberately no non-container fallback: when the engine is
unreachable `SiteboxUnavailable` carries a message the tools surface verbatim,
and everything read-only keeps working because it never touches the container.
"""

import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

from app import config, db

IMAGE_REPO = "cappuccina-site"
# Absolute and cwd-independent: the backend may be started from anywhere.
IMAGE_CONTEXT = Path(__file__).resolve().parents[1] / "sitebox"

EXEC_TIMEOUT_GRACE = 30  # the CLI gets this much longer than the shell itself
IMAGE_BUILD_TIMEOUT = 600  # cold `npm`-less node pull + apt layer
MACHINE_START_TIMEOUT = 120  # `podman machine start` on a cold machine

# Merged output is capped at 64 KB: a runaway build must not be able to blow
# up an SSE frame or the DB.
MAX_OUTPUT_BYTES = 64 * 1024
_TRUNCATE_HEAD_BYTES = 48 * 1024
_TRUNCATE_TAIL_BYTES = 8 * 1024
_TRUNCATE_MARKER = "\n… output truncated …\n"

_SEEDED_GITIGNORE = "node_modules/\ndist/\n.npm-cache/\n*.log\n"

# What the CLIs print when their engine/machine is not reachable. Matching the
# CLI's own wording (rather than a bare "cannot connect") keeps a user command
# that happens to log a connection error from being misread as engine loss.
_CONNECTION_MARKERS = (
    "cannot connect to podman",
    "unable to connect to podman",
    "cannot connect to the docker daemon",
    "error during connect",
    "is the docker daemon running",
)

UNAVAILABLE_MESSAGE = (
    "The website workspace isn't available on this server right now. "
    "Try again in a moment."
)


class SiteboxUnavailable(Exception):
    """The container engine or image could not be used for this workspace."""


# site_id -> monotonic() of the last create/exec. Single-process bookkeeping,
# exactly like the _WEBPAGE_BUSY set in main.py: a second worker would only
# misjudge idle-stop timing, never corrupt a workspace.
last_used: dict[str, float] = {}


def sites_root() -> Path:
    """Host directory holding one workspace per site.

    Workspaces are fanned out into 256 buckets named by the first two hex
    characters of the site id, so no single directory accumulates unbounded
    entries. Resolved on every call (never at import) so tests that repoint
    db.DATA_DIR get an isolated site root for free.
    """
    return db.DATA_DIR / "sites"


def _container_name(site_id: str) -> str:
    return f"capweb-{site_id}"


def _preview_base(site: dict) -> str:
    """Root-absolute base a site's build output is expected to use."""
    return f"/api/v1/webpages/{site['chat_id']}/preview/{site['preview_key']}/"


def workspace_dir(site_id: str) -> Path:
    """Return (creating if needed) the host directory bound to /workspace.

    The workspace lives at sites/<site_id[:2]>/<site_id> so the sites tree does
    not grow one flat directory per site. The container runs as the
    unprivileged `node` user, so the directory has to be world-writable; chmod
    is best-effort because it is a no-op-ish on Windows and can be refused on
    some mounts.
    """
    path = sites_root() / site_id[:2] / site_id
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o777)
    except OSError:
        pass
    # Seeded so git history and the file tree stay clean: node_modules lives on
    # its own volume, and dist/ artifacts are rebuilt rather than committed.
    seeded = path / ".gitignore"
    if not seeded.exists():
        seeded.write_text(_SEEDED_GITIGNORE, encoding="utf-8")
    return path


def _is_podman() -> bool:
    return Path(config.SITE_CONTAINER_CLI).name.lower().startswith("podman")


def _is_connection_error(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _CONNECTION_MARKERS)


def available() -> bool:
    """True when the CLI is on PATH and its engine answers.

    Never raises: callers use this to degrade gracefully (and tests use it as
    a skip condition), so an absent or hung engine simply reads as False.
    """
    cli = config.SITE_CONTAINER_CLI
    if shutil.which(cli) is None:
        return False
    try:
        result = subprocess.run(
            [cli, "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _failure(message: str) -> subprocess.CompletedProcess:
    """A synthetic failed CLI result, so every error path is one code path."""
    return subprocess.CompletedProcess(args=[], returncode=1, stdout=message)


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run the container CLI with stdout+stderr merged; never raises.

    Failures that happen before the CLI can answer at all (missing binary,
    hung process) come back as a nonzero result carrying the reason, so
    callers only ever have to inspect returncode and output.
    """
    try:
        return subprocess.run(
            [config.SITE_CONTAINER_CLI, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        cli = config.SITE_CONTAINER_CLI
        return _failure(f"The {cli} command timed out after {timeout} seconds.")
    except OSError as exc:  # CLI missing, not executable, ...
        return _failure(str(exc))


def _start_machine() -> None:
    """Best-effort `podman machine start`; a failure is reported by the retry."""
    _run(["machine", "start"], timeout=MACHINE_START_TIMEOUT)


def _cli(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run the container CLI, transparently starting the machine once.

    An unreachable engine raises SiteboxUnavailable, so callers can treat
    every returned result as a real answer from a live engine.
    """
    result = _run(args, timeout)
    if _is_connection_error(result.stdout or ""):
        if _is_podman():
            _start_machine()
            result = _run(args, timeout)
        if _is_connection_error(result.stdout or ""):
            raise SiteboxUnavailable(UNAVAILABLE_MESSAGE)
    return result


def _failure_tail(result: subprocess.CompletedProcess) -> str:
    """The CLI's own words, trimmed to something short enough for a chat."""
    detail = (result.stdout or "").strip()
    return detail[-200:] if detail else "The container engine rejected the request."


def image_ref() -> str:
    """The image to build and run, tagged with the Containerfile's contents.

    `image exists` is the build gate, so a fixed tag would keep serving the
    first image ever built and an edited Containerfile (a new package, say)
    would silently never take effect. Hashing the file into the tag rebuilds
    once per edit and reuses that image afterwards; superseded tags stay on
    disk until `podman image prune`.
    """
    try:
        digest = hashlib.sha256(
            (IMAGE_CONTEXT / "Containerfile").read_bytes()
        ).hexdigest()
    except OSError:
        return f"{IMAGE_REPO}:latest"
    return f"{IMAGE_REPO}:{digest[:12]}"


def ensure_image() -> None:
    """Build the site image unless the engine already has it.

    Gated on `image exists` because a cold build (node base pull + apt layer)
    costs tens of seconds and must happen at most once per machine.
    """
    result = _cli(["image", "exists", image_ref()], timeout=60)
    if result.returncode == 0:
        return
    build = _cli(
        ["build", "-t", image_ref(), str(IMAGE_CONTEXT)], timeout=IMAGE_BUILD_TIMEOUT
    )
    if build.returncode != 0:
        raise SiteboxUnavailable(
            f"Could not build the website workspace image: {_failure_tail(build)}"
        )


def _container_status(name: str) -> str | None:
    """`running` / `exited` / ... or None when no such container exists."""
    result = _cli(["inspect", "--format", "{{.State.Status}}", name], timeout=60)
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _create(name: str, workspace: Path, site: dict) -> None:
    """Create the site container with the frozen sandbox flags.

    The container is long-lived (`sleep infinity`) and commands are exec'd
    into it, so package installs and `.git` state survive between tool calls.
    The capability set is the least that lets the image's apt-get-only sudo
    work (see the flags below).
    """
    image = image_ref()
    result = _cli(
        [
            "create",
            "--name",
            name,
            "--workdir",
            "/workspace",
            "--user",
            "node",
            "--env",
            f"PREVIEW_BASE={_preview_base(site)}",
            "--volume",
            f"{workspace}:/workspace",
            "--volume",
            f"capweb-nm-{site['id']}:/workspace/node_modules",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--pids-limit",
            "512",
            # Everything is dropped and only apt's six come back. The image's
            # one sudoers rule allows apt-get/apt/dpkg and nothing else, so
            # the model's own commands stay unprivileged (`sudo rm` is refused
            # for a password). SETUID/SETGID let sudo switch to root, which is
            # also why `no-new-privileges` is gone — it makes setuid-root sudo
            # refuse to run at all. CHOWN, DAC_OVERRIDE, FOWNER and FSETID are
            # dpkg's: it chowns what it unpacks, writes into `_apt`-owned dirs
            # and sets setuid bits.
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "FOWNER",
            "--cap-add",
            "FSETID",
            image,
            "sleep",
            "infinity",
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise SiteboxUnavailable(
            f"Could not create the website workspace: {_failure_tail(result)}"
        )


def _start(name: str) -> None:
    result = _cli(["start", name], timeout=MACHINE_START_TIMEOUT)
    if result.returncode != 0:
        raise SiteboxUnavailable(
            f"Could not start the website workspace: {_failure_tail(result)}"
        )


# One container per site: two parallel tool calls (a write and a shell, say)
# would otherwise both inspect-then-create it and one would fail with "name is
# already in use". The lock covers only the cheap lifecycle probe, not the
# command itself.
_lifecycle_lock = threading.Lock()


def ensure_running(site: dict) -> None:
    """Create the site's container if missing, start it if stopped."""
    name = _container_name(site["id"])
    # The bind-mount source has to exist before `create` runs.
    workspace = workspace_dir(site["id"])
    with _lifecycle_lock:
        status = _container_status(name)
        if status != "running":
            if status is None:
                ensure_image()
                _create(name, workspace, site)
            _start(name)
    last_used[site["id"]] = time.monotonic()


def drop_unreadable_links(workspace: Path) -> None:
    """Remove host-side symlink entries that Windows git cannot read.

    The container is a Linux VM writing into a bind-mounted Windows folder:
    `ln -s` (and scaffolds like create-astro's CLAUDE.md → AGENTS.md) leaves a
    WSL reparse point that git for Windows cannot open, and one stray link
    makes `git add -A` fail outright — which would fail a run's versioning.
    A site the preview serves has no use for a symlink, so they are dropped
    while the folder is writable. No-op on POSIX hosts, where git reads them.
    """
    if os.name != "nt":
        return
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    for root, dirs, files in os.walk(workspace):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in [*dirs, *files]:
            path = Path(root) / name
            try:
                attrs = os.lstat(path).st_file_attributes
            except (OSError, AttributeError):
                continue
            # Real Windows symlinks (`os.path.islink`) are indexable; the WSL
            # kind is a plain-looking file with the reparse attribute set.
            if not attrs & reparse or os.path.islink(path):
                continue
            try:
                os.unlink(path)
            except OSError:
                try:
                    os.rmdir(path)
                except OSError:
                    continue
            if name in dirs:
                dirs.remove(name)


def _cap_output(text: str) -> str:
    """Cap merged output: first 48 KB + marker + last 8 KB.

    Sliced as bytes (then decoded) so a UTF-8 sequence split at the cut point
    degrades to a replacement character instead of raising.
    """
    data = text.encode("utf-8")
    if len(data) <= MAX_OUTPUT_BYTES:
        return text
    head = data[:_TRUNCATE_HEAD_BYTES].decode("utf-8", errors="replace")
    tail = data[-_TRUNCATE_TAIL_BYTES:].decode("utf-8", errors="replace")
    return head + _TRUNCATE_MARKER + tail


def run_command(site: dict, command: str, timeout: int) -> tuple[int, str]:
    """Run `command` as bash in the site's container; (exit_code, output).

    The command runs under coreutils `timeout` inside the container so a
    runaway process tree dies even if the CLI is killed later; the Python-side
    timeout is the backstop for a wedged CLI.
    """
    ensure_running(site)
    name = _container_name(site["id"])
    args = [
        "exec",
        "--workdir",
        "/workspace",
        "--user",
        "node",
        name,
        "bash",
        "-c",
        f"timeout -k 5 {timeout} bash -c {shlex.quote(command)}",
    ]
    try:
        result = subprocess.run(
            [config.SITE_CONTAINER_CLI, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout + EXEC_TIMEOUT_GRACE,
        )
    except subprocess.TimeoutExpired:
        return 124, f"Command timed out after {timeout} seconds."
    except OSError as exc:
        raise SiteboxUnavailable(UNAVAILABLE_MESSAGE) from exc
    output = result.stdout or ""
    if result.returncode != 0 and _is_connection_error(output):
        raise SiteboxUnavailable(UNAVAILABLE_MESSAGE)
    if result.returncode == 124:
        return 124, f"Command timed out after {timeout} seconds."
    # A command may have created a WSL symlink the host cannot serve or index;
    # drop those while the workspace is fresh (see drop_unreadable_links).
    try:
        drop_unreadable_links(workspace_dir(site["id"]))
    except OSError:
        pass
    return result.returncode, _cap_output(output)


def has_build(workspace: Path) -> bool:
    """True when package.json exists and declares a non-empty build script.

    Read host-side: whether a rebuild is needed never requires the container.
    """
    try:
        package = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(package, dict):
        return False
    scripts = package.get("scripts")
    script = scripts.get("build") if isinstance(scripts, dict) else None
    return isinstance(script, str) and bool(script.strip())


def run_build(site: dict) -> tuple[bool, str]:
    """`npm run build` in the site; (ok, failure output)."""
    exit_code, output = run_command(site, "npm run build", config.SITE_BUILD_TIMEOUT)
    return exit_code == 0, "" if exit_code == 0 else output


def stop_idle() -> None:
    """Stop containers that have not been used for SITE_IDLE_MINUTES.

    Best-effort: a failed stop (engine down) is forgotten rather than
    retried, because a stale entry would otherwise be swept forever.
    """
    idle_after = config.SITE_IDLE_MINUTES * 60
    now = time.monotonic()
    for site_id, used_at in list(last_used.items()):
        if now - used_at < idle_after:
            continue
        _run(["stop", _container_name(site_id)], timeout=MACHINE_START_TIMEOUT)
        del last_used[site_id]


def finalize_run(
    user_id: str, chat_id: str, message_uid: str | None
) -> tuple[str | None, dict | None]:
    """Fold a finished run into one iteration, then rebuild if the site has one.

    Returns (commit sha or None, {"ok", "error"} or None). Nothing is written
    when the site does not exist or the run changed no files, so an empty
    reply never produces an empty commit.
    """
    site = db.get_webpage_site(user_id, chat_id)
    if site is None:
        return None, None
    # Imported here: webpage_git imports this module for workspace paths, so a
    # module-level import would be circular.
    from app import webpage_git

    sha = webpage_git.commit_workspace(
        site, origin="ai", label="", message_uid=message_uid
    )
    if sha is None:
        return None, None
    build = None
    if has_build(workspace_dir(site["id"])):
        try:
            ok, error = run_build(site)
        except SiteboxUnavailable as exc:
            # The saves already landed on the host; a dead runtime must not
            # take down the run, it just means this rebuild was skipped.
            ok, error = False, str(exc)
        build = {"ok": ok, "error": error or None}
    return sha, build
