"""Git history for a chat's website workspace.

The workspace folder is the whole truth: the untouched files a site is made of
live on disk (sitebox owns the directory), and git in that directory is the
history layer. The database keeps only the site registry and the active commit
sha — no file text, no per-iteration snapshots. One direct consequence is that
history, diffs and the preview keep working while the container is stopped
(and while it is unavailable), because every call here is plain host git.

An "iteration" is one commit carrying an `Iter: N` trailer, tagged `iter/N`.
Tags are what define history: an iteration that was activated away is still
reachable from its children, but dropping its tag (see the prune in
`list_versions`) is what retires it, exactly like the old DB cap — and a diff
whose base is gone falls back to "everything added".
"""

import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from app import db, sitebox

# Iterations kept before the oldest non-active one is retired. Same number the
# SQLite history used; the frontend's iteration bar assumes nothing beyond it.
GIT_VERSION_LIMIT = 50

# Rendered diff lines across all files of one response, same as before.
GIT_DIFF_LINE_LIMIT = 2000

# Git's well-known empty tree object. Diffing a root commit against it is how a
# commit with no base reads as "everything added" without a special case.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# No pager and no terminal prompt ever: a request thread must not block on a
# pager or on a credential question. os.environ is copied because subprocess
# wants a full environment (git needs PATH, HOME, ...).
_GIT_ENV = {**os.environ, "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"}

# One line of a commit body trailer ("Origin: ai", "Iter: 3", ...).
_TRAILER_RE = re.compile(r"^(?P<name>[A-Za-z0-9-]+):[ \t]*(?P<value>.*)$")


def _git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run one host git command with the workspace as the working directory.

    Read-only probes pass `check=False` and inspect `returncode`; everything
    else lets a real failure (corrupt repo, no identity) surface instead of
    silently producing a bogus sha. Decoding is utf-8 with replacement so a
    binary file in a diff can never raise.
    """
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        env=_GIT_ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _workspace(site: dict) -> Path:
    """Where this site's files and .git live. Resolved per call so isolated
    test runs (which repoint the data dir) get their own directory."""
    return sitebox.workspace_dir(site["id"])


def init_workspace(path: Path) -> None:
    """Create the git repo a workspace's history lives in (idempotent).

    `git init` on an existing repo is a no-op, so this doubles as an "make sure
    it is a repo" call. The identity and settings are local: commits must work
    on a machine with no global git config, `core.autocrlf false` keeps the
    bytes we wrote as the bytes git stores, and a global `commit.gpgsign` would
    otherwise hang or fail every commit. Serialized (reentrant, `ensure_repo`
    nests) because two inits at once collide on the template files.
    """
    path.mkdir(parents=True, exist_ok=True)
    with _init_lock:
        _git(path, "init")
        for key, value in (
            ("user.name", "Cappuccina"),
            ("user.email", "webpage@cappuccina.local"),
            ("core.autocrlf", "false"),
            ("commit.gpgsign", "false"),
        ):
            _git(path, "config", "--local", key, value)


# `git init` copies the template hooks and refs; two inits at once collide on
# those files and exit 128 ("cannot copy ... File exists"). Parallel tool calls
# both ensure the repo before their first write, so repo setup is serialized.
# Reentrant: `ensure_repo` holds it while calling `init_workspace`.
_init_lock = threading.RLock()


def ensure_repo(workspace: Path) -> None:
    """Make sure the workspace has a git repo, safe under parallel tool calls."""
    if (workspace / ".git").exists():
        return
    with _init_lock:
        if not (workspace / ".git").exists():
            init_workspace(workspace)


def _trailers(message: str) -> dict[str, str]:
    """The `Name: value` lines of a commit body — the subject is skipped."""
    trailers: dict[str, str] = {}
    for line in message.splitlines()[1:]:
        match = _TRAILER_RE.match(line)
        if match:
            trailers[match.group("name")] = match.group("value").strip()
    return trailers


def _message_label(message: str) -> str:
    """First line of a commit message (the iteration's label; "" when none)."""
    lines = message.splitlines()
    return lines[0] if lines else ""


def _iter_of(trailers: dict[str, str]) -> int | None:
    try:
        return int(trailers["Iter"])
    except (KeyError, ValueError):
        return None


def _uid_of(trailers: dict[str, str]) -> str | None:
    """The message a commit answers to, or None — the trailer writes "-" when
    the change came from somewhere other than a chat reply."""
    value = trailers.get("Message-Uid", "")
    return value if value and value != "-" else None


def _iso(epoch: str) -> str:
    """A `%ct` unix timestamp as the ISO-8601 UTC string rows have always had."""
    try:
        return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


# %B (the raw body) goes last so the record separator cannot be confused with
# an empty label: a message may legitimately start with blank lines.
_LOG_FORMAT = "%H%x1f%P%x1f%ct%x1f%B%x1e"
_NUMSTAT_FORMAT = "%x01%H"


def _log_records(workspace: Path) -> list[dict]:
    """Every reachable commit as {sha, parents, ct, message}.

    A brand-new repo has no commits, so a failing `git log` means "no history"
    rather than an error. `--all` also covers HEAD, which matters after a
    checkout: new commits land detached on top of the active one and still
    appear here.
    """
    out = _git(workspace, "log", "--all", "--no-color", f"--format={_LOG_FORMAT}", check=False)
    if out.returncode != 0:
        return []
    records: list[dict] = []
    for chunk in out.stdout.split("\x1e"):
        # The character before a record is the previous record's newline; the
        # message's own leading newlines are behind the field separator.
        chunk = chunk.lstrip("\n")
        if not chunk:
            continue
        fields = chunk.split("\x1f")
        if len(fields) < 4:
            continue
        records.append(
            {
                "sha": fields[0].strip(),
                "parents": fields[1].split(),
                "ct": fields[2].strip(),
                "message": "\x1f".join(fields[3:]),
            }
        )
    return records


def _commit_summaries(workspace: Path) -> dict[str, dict]:
    """{sha: {files, added, removed}} for every reachable commit.

    The Changes tab shows these counts, so they come from git's own numstat
    rather than a re-parse of the rendered diff. A binary file counts as one
    file with "-" for both sides.
    """
    out = _git(
        workspace, "log", "--all", "--numstat", f"--format={_NUMSTAT_FORMAT}", check=False
    )
    if out.returncode != 0:
        return {}
    totals: dict[str, list[int]] = {}
    current: str | None = None
    for line in out.stdout.splitlines():
        if line.startswith("\x01"):
            current = line[1:].strip()
            totals[current] = [0, 0, 0]
            continue
        if current is None or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        if "\t".join(parts[2:]) == ".gitignore":
            # The seeded plumbing file, not site content: counting it would
            # make every first iteration read "+1 file" bigger than it was.
            continue
        totals[current][0] += 1
        if parts[0] != "-":
            totals[current][1] += int(parts[0])
        if parts[1] != "-":
            totals[current][2] += int(parts[1])
    return {
        sha: {"files": counts[0], "added": counts[1], "removed": counts[2]}
        for sha, counts in totals.items()
    }


def _tagged_commits(workspace: Path) -> dict[str, int]:
    """{sha: seq} for every `iter/N` tag. Tags, not reachability, are history:
    a retired iteration stays in the object store but is no longer pointed at."""
    out = _git(
        workspace,
        "for-each-ref",
        # A plain space separator: for-each-ref does not expand %x1f escapes.
        "--format=%(refname:short) %(objectname)",
        "refs/tags/iter/",
        check=False,
    )
    tagged: dict[str, int] = {}
    for line in out.stdout.splitlines():
        name, _, sha = line.partition(" ")
        match = re.fullmatch(r"iter/(\d+)", name.strip())
        if match and sha.strip():
            tagged[sha.strip()] = int(match.group(1))
    return tagged


def _next_seq(workspace: Path) -> int:
    """1 + the highest iteration number ever committed or tagged.

    Trailers are read from reachable commits as well as live tags so a retired
    (tag-deleted) iteration can never have its number reused — the commit body
    is the record that survives.
    """
    highest = 0
    for record in _log_records(workspace):
        seq = _iter_of(_trailers(record["message"]))
        if seq is not None and seq > highest:
            highest = seq
    for seq in _tagged_commits(workspace).values():
        if seq > highest:
            highest = seq
    return highest + 1


def commit_workspace(site: dict, origin: str, label: str, message_uid: str | None) -> str | None:
    """Version whatever changed in the workspace as one iteration.

    Returns None when nothing changed (no empty commits), else the new sha,
    which also becomes the site's active commit. `label` is the message's
    first line and may be empty (AI runs); the trailers are how the iteration
    list finds origin, order and the message it belongs to.
    """
    workspace = _workspace(site)
    ensure_repo(workspace)
    # One unreadable WSL symlink would make `git add -A` fail outright; drop
    # such entries first (no-op off Windows).
    sitebox.drop_unreadable_links(workspace)
    status = _git(workspace, "status", "--porcelain")
    if not status.stdout.strip():
        return None
    # `git add -A` respects the seeded .gitignore, so node_modules/ and dist/
    # never enter history.
    _git(workspace, "add", "-A")
    seq = _next_seq(workspace)
    message = f"{label}\n\nOrigin: {origin}\nIter: {seq}\nMessage-Uid: {message_uid or '-'}"
    # --cleanup=verbatim is what makes the message exactly the one above: the
    # default cleanup would eat the blank first line an empty label produces.
    _git(workspace, "commit", "--cleanup=verbatim", "-m", message)
    sha = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    # Lightweight tag: it keeps the commit out of garbage collection and is
    # what marks it as an iteration after HEAD moves on.
    _git(workspace, "tag", f"iter/{seq}")
    db.set_webpage_active_commit(site["user_id"], site["chat_id"], sha)
    db.touch_webpage_site(site["user_id"], site["chat_id"])
    return sha


def is_version(site: dict, commit: str) -> bool:
    """Whether this sha is a live iteration — i.e. has an `iter/*` tag.

    Routes 404 on anything else: an arbitrary commit (or a retired one) is not
    something the frontend may activate or diff.
    """
    if not commit:
        return False
    workspace = _workspace(site)
    if not (workspace / ".git").exists():
        return False
    out = _git(workspace, "tag", "--list", "iter/*", "--points-at", commit, check=False)
    return out.returncode == 0 and bool(out.stdout.strip())


def _prune(workspace: Path, rows: list[dict], active_commit: str | None) -> None:
    """Retire the oldest iterations past GIT_VERSION_LIMIT — never the active one.

    Only the tag is deleted: the commit stays reachable through its child, so
    pruning is cheap and reversible-by-nothing, while a later diff whose base
    was retired falls back to "everything added" (see `version_diff`).
    """
    while len(rows) > GIT_VERSION_LIMIT:
        victim = next((row for row in rows if row["id"] != active_commit), None)
        if victim is None:
            return
        _git(workspace, "tag", "-d", f"iter/{victim['seq']}", check=False)
        rows.remove(victim)


def list_versions(site: dict) -> list[dict]:
    """Iterations, oldest first, in the shape the canvas has always consumed.

    Kept is a commit that carries an `Iter: N` trailer *and* is pointed at by
    an `iter/*` tag — so stray commits the model or a person makes by hand
    never show up, and a retired iteration drops out.
    """
    workspace = _workspace(site)
    if not (workspace / ".git").exists():
        return []
    tagged = _tagged_commits(workspace)
    if not tagged:
        return []
    summaries = _commit_summaries(workspace)
    rows: list[dict] = []
    for record in _log_records(workspace):
        if record["sha"] not in tagged:
            continue
        trailers = _trailers(record["message"])
        seq = _iter_of(trailers)
        if seq is None:
            continue
        rows.append(
            {
                "id": record["sha"],
                "seq": seq,
                "parentVersionId": record["parents"][0] if record["parents"] else None,
                "origin": trailers.get("Origin", ""),
                "label": _message_label(record["message"]),
                "messageUid": _uid_of(trailers),
                "assets": [],
                "summary": summaries.get(
                    record["sha"], {"files": 0, "added": 0, "removed": 0}
                ),
                "createdAt": _iso(record["ct"]),
            }
        )
    rows.sort(key=lambda row: row["seq"])
    _prune(workspace, rows, site.get("active_commit"))
    return rows


def _resolve(workspace: Path, commit: str) -> bool:
    """Whether the workspace knows this string as a commit."""
    out = _git(workspace, "cat-file", "-e", f"{commit}^{{commit}}", check=False)
    return out.returncode == 0


def _numstat(text: str) -> dict[str, tuple[int | None, int | None]]:
    """{path: (added, removed)}; None marks a binary file's "-" side."""
    counts: dict[str, tuple[int | None, int | None]] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed, path = parts[0], parts[1], "\t".join(parts[2:])
        counts[path] = (
            None if added == "-" else int(added),
            None if removed == "-" else int(removed),
        )
    return counts


def _strip_prefix(path: str) -> str:
    """Drop git's `a/` or `b/` side prefix from a diff path."""
    return path[2:] if path[:2] in ("a/", "b/") else path


def _binary_paths(line: str) -> str | None:
    """The path out of "Binary files a/x and b/x differ" (side may be /dev/null)."""
    body = line[len("Binary files ") :]
    if body.endswith(" differ"):
        body = body[: -len(" differ")]
    for side in body.split(" and "):
        if side != "/dev/null":
            return _strip_prefix(side.strip())
    return None


def _diff_blocks(text: str) -> dict[str, dict]:
    """{path: {status, lines}} parsed from one unified diff.

    The status comes from the block header ("new file mode" / "deleted file
    mode" / otherwise modified); the path from the `--- `/`+++ ` lines or, for
    binaries, from the "Binary files ... differ" line (they have no ---/+++
    lines). Everything before the first hunk is metadata and is discarded, so
    the rendered lines are exactly hunks, additions, deletions and context.
    """
    parsed: list[dict] = []
    current: dict | None = None
    in_hunk = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current = {"status": "modified", "path": None, "lines": []}
            parsed.append(current)
            in_hunk = False
            continue
        if current is None:
            continue
        if not in_hunk:
            if line.startswith("new file mode"):
                current["status"] = "added"
            elif line.startswith("deleted file mode"):
                current["status"] = "removed"
            elif line.startswith("Binary files ") and line.endswith(" differ"):
                current["path"] = _binary_paths(line)
            elif line.startswith("--- ") and line[4:] != "/dev/null":
                current["path"] = _strip_prefix(line[4:])
            elif line.startswith("+++ ") and line[4:] != "/dev/null":
                current["path"] = _strip_prefix(line[4:])
            elif line.startswith("@@"):
                in_hunk = True
                current["lines"].append({"kind": "hunk", "text": line})
            continue
        if line.startswith("@@"):
            current["lines"].append({"kind": "hunk", "text": line})
        elif line.startswith("+"):
            current["lines"].append({"kind": "add", "text": line[1:]})
        elif line.startswith("-"):
            current["lines"].append({"kind": "del", "text": line[1:]})
        elif line.startswith(" "):
            current["lines"].append({"kind": "ctx", "text": line[1:]})
        elif line == "":
            current["lines"].append({"kind": "ctx", "text": ""})
        # Anything else (e.g. "\ No newline at end of file") is not a body line.
    # Keyed by path: a block whose path never appeared (empty diff of a mode
    # change, say) is not reachable from numstat and is dropped.
    return {block["path"]: block for block in parsed if block["path"]}


def version_diff(site: dict, commit: str, against: str | None = None) -> dict | None:
    """Unified diff of one iteration against an explicit base or its parent.

    None when the sha is not a live iteration (the route 404s). A base that no
    longer exists — a retired parent, a bogus `against` — degrades to the empty
    tree, i.e. "everything added", never an error, which is what the previous
    storage did when an iteration's base had been pruned.
    """
    if not is_version(site, commit):
        return None
    workspace = _workspace(site)
    base: str | None
    if against:
        base = against if _resolve(workspace, against) else None
    else:
        parents = _git(workspace, "log", "-1", "--format=%P", commit, check=False).stdout.split()
        base = parents[0] if parents else None
    root = base or EMPTY_TREE
    counted = _numstat(_git(workspace, "diff", "--numstat", root, commit, check=False).stdout)
    blocks = _diff_blocks(
        _git(workspace, "diff", "--no-color", "-U3", root, commit, check=False).stdout
    )
    out: list[dict] = []
    budget = GIT_DIFF_LINE_LIMIT
    truncated = False
    for path in sorted(counted):
        if budget <= 0:
            truncated = True
            break
        added, removed = counted[path]
        block = blocks.get(path)
        if added is None or removed is None:
            # Binary: one describing line stands in for the content, and
            # neither side has countable lines.
            added = 0
            removed = 0
            lines = [{"kind": "ctx", "text": "binary file"}]
        else:
            lines = block["lines"] if block else []
        if not lines:
            # A mode-only change (or a path git could not render) has nothing
            # for the Changes tab to show.
            continue
        if len(lines) > budget:
            lines = lines[:budget]
            truncated = True
        budget -= len(lines)
        out.append(
            {
                "path": path,
                "status": block["status"] if block else "modified",
                "added": added,
                "removed": removed,
                "lines": lines,
            }
        )
    return {
        "versionId": commit,
        "baseVersionId": base or None,
        "files": out,
        "truncated": truncated,
    }


def checkout(site: dict, commit: str) -> None:
    """Make the working copy exactly this iteration.

    Detached and forced so a dirty or branch-y tree cannot refuse; `clean -fd`
    removes leftovers the snapshot did not have. Never `-x`: node_modules/ and
    dist/ are ignored and must survive, or activating an iteration would throw
    away the installed dependencies and the served build.
    """
    workspace = _workspace(site)
    _git(workspace, "checkout", "--detach", "-f", commit)
    _git(workspace, "clean", "-fd")


def changed_files(site: dict, commit: str | None = None) -> list[str]:
    """Names the tools and the canvas list.

    With no commit this is the workspace tree — tracked plus untracked, with
    .gitignore'd noise (node_modules/, dist/) left out — which is what the
    model sees. With a commit it is what changed since that snapshot.
    """
    workspace = _workspace(site)
    if commit is None:
        # A workspace with files but no repo yet must still list; the write
        # path creates the repo, this just makes the listing order-independent.
        ensure_repo(workspace)
        names = _git(
            workspace, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
        ).stdout.split("\x00")
        # The seeded .gitignore is plumbing the user never wrote; the canvas
        # and the model's file listing should not show it.
        names = [name for name in names if name != ".gitignore"]
    else:
        if not (workspace / ".git").exists():
            return []
        names = _git(workspace, "diff", "--name-only", commit, check=False).stdout.splitlines()
        untracked = _git(
            workspace, "ls-files", "-z", "--others", "--exclude-standard", check=False
        ).stdout
        names += untracked.split("\x00")
    return sorted({name.replace("\\", "/") for name in names if name.strip()})
