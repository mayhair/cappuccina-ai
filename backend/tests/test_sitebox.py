"""The container runtime: image, lifecycle, exec and one npm build.

Needs a reachable Podman (or Docker) engine, so the whole module is skipped
without one. Every test removes its container and node_modules volume
afterwards, so a red test never leaves a workspace container behind.
"""

import json
import os
import subprocess
import uuid

import pytest

from app import config, sitebox

pytestmark = pytest.mark.skipif(
    not sitebox.available(), reason="podman engine unavailable"
)


def _site() -> dict:
    """A site row-shaped dict with an id no other test can collide with."""
    site_id = uuid.uuid4().hex
    return {
        "id": site_id,
        "user_id": "sitebox-probe-user",
        "chat_id": f"sitebox-probe-chat-{site_id}",
        "preview_key": f"sitebox-probe-key-{site_id}",
        "active_commit": None,
    }


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [config.SITE_CONTAINER_CLI, *args], capture_output=True, text=True
    )


def _status(site_id: str) -> str:
    """`running` / `exited` as the engine itself reports it."""
    return _cli("inspect", "--format", "{{.State.Status}}", f"capweb-{site_id}").stdout.strip()


def _cleanup(site_id: str) -> None:
    _cli("rm", "-f", f"capweb-{site_id}")
    _cli("volume", "rm", "-f", f"capweb-nm-{site_id}")


@pytest.fixture
def site(isolated_db):
    """A fresh site dict; its container and volume are removed afterwards."""
    site = _site()
    try:
        yield site
    finally:
        _cleanup(site["id"])


def test_available_reports_the_engine():
    assert sitebox.available() is True


def test_ensure_image_leaves_a_usable_image():
    sitebox.ensure_image()
    assert _cli("image", "exists", sitebox.image_ref()).returncode == 0


def test_ensure_running_creates_then_starts_the_container(site):
    sitebox.ensure_running(site)
    assert _status(site["id"]) == "running"

    # Idempotent: a running container is left alone.
    sitebox.ensure_running(site)
    assert _status(site["id"]) == "running"


def test_run_command_runs_bash_and_passes_exit_codes(site):
    assert sitebox.run_command(site, "echo hi", 30) == (0, "hi\n")

    code, output = sitebox.run_command(site, "echo boom >&2; exit 3", 30)
    assert code == 3
    assert "boom" in output


def test_run_command_times_out_with_the_documented_message(site):
    assert sitebox.run_command(site, "sleep 30", 2) == (
        124,
        "Command timed out after 2 seconds.",
    )


def test_run_build_leaves_the_build_output_on_the_host(site):
    workspace = sitebox.workspace_dir(site["id"])
    # A one-file "build": enough to prove npm is wired to the bind mount
    # without pulling a framework over the network.
    (workspace / "package.json").write_text(
        json.dumps({"scripts": {"build": "mkdir -p dist && cp index.html dist/"}}),
        encoding="utf-8",
    )
    (workspace / "index.html").write_text("<title>built</title>", encoding="utf-8")
    assert sitebox.has_build(workspace) is True

    assert sitebox.run_build(site) == (True, "")
    assert (workspace / "dist" / "index.html").read_text(encoding="utf-8") == (
        "<title>built</title>"
    )


def test_container_has_curl_and_apt_via_a_restricted_sudo(site):
    """The model's escape hatch for non-node tools.

    The image ships curl, and a missing package can be installed with `sudo
    apt-get install`: one sudoers rule (apt-get/apt/dpkg) backed by the six
    capabilities sitebox adds back after `--cap-drop ALL`. This test is what
    fails if either half regresses — the image's packages or the container's
    capabilities.
    """
    code, output = sitebox.run_command(site, "curl --version", 60)
    assert code == 0 and output.startswith("curl "), output

    # sudo reaches apt-get and nothing else (the password it can't supply is
    # what every other command would need).
    code, output = sitebox.run_command(site, "sudo -n apt-get --version", 60)
    assert code == 0 and output.startswith("apt "), output
    code, output = sitebox.run_command(site, "sudo -n rm -rf index.html", 60)
    assert code != 0 and "password is required" in output, output

    # A real install, end to end: the image ships no package lists.
    code, output = sitebox.run_command(
        site, "sudo apt-get update -qq && sudo apt-get install -y -qq bc", 300
    )
    assert code == 0, output
    assert sitebox.run_command(site, "echo '2+3' | bc", 60) == (0, "5\n")


def test_stop_idle_stops_and_the_next_command_restarts(site, monkeypatch):
    sitebox.ensure_running(site)
    assert sitebox.run_command(site, "echo hi", 30) == (0, "hi\n")

    monkeypatch.setattr(sitebox.config, "SITE_IDLE_MINUTES", 0)
    sitebox.stop_idle()
    assert _status(site["id"]) == "exited"

    # The next command needs the container, so it starts it again.
    assert sitebox.run_command(site, "echo again", 30) == (0, "again\n")
    assert _status(site["id"]) == "running"


@pytest.mark.skipif(
    os.name != "nt",
    reason="WSL reparse points only exist on Windows hosts",
)
def test_container_symlinks_are_dropped_so_git_can_stage(site):
    """create-astro writes CLAUDE.md → AGENTS.md as a WSL symlink (its --yes
    scaffold does), and such a reparse point makes `git add -A` fail outright
    on Windows — the whole run's versioning would die on it."""
    from app import webpage_git

    workspace = sitebox.workspace_dir(site["id"])
    code, output = sitebox.run_command(
        site, "printf x > AGENTS.md && ln -s AGENTS.md CLAUDE.md", 60
    )
    assert code == 0, output

    assert os.path.lexists(workspace / "AGENTS.md")
    # run_command drops the unreadable link as soon as the command returns, so
    # the file tree, the preview and the commit never meet it.
    assert not os.path.lexists(workspace / "CLAUDE.md")
    assert webpage_git.commit_workspace(site, origin="ai", label="", message_uid="r1")


def test_destroy_site_removes_container_volume_and_folder(site):
    """What chat and account deletion rely on: nothing of the site survives."""
    workspace = sitebox.workspace_dir(site["id"])
    code, output = sitebox.run_command(site, "printf hi > index.html", 60)
    assert code == 0, output
    assert _status(site["id"]) == "running"

    sitebox.destroy_site(site["id"])

    assert _cli("container", "exists", f"capweb-{site['id']}").returncode != 0
    assert _cli("volume", "exists", f"capweb-nm-{site['id']}").returncode != 0
    assert not workspace.exists()
    assert site["id"] not in sitebox.last_used
