"""The shared sqlite connection under parallel tool execution.

Sync tools run in worker threads (LangChain runs them in an executor) and a
model can emit several tool calls in one message, which the tool node gathers.
Every path below used to expose the one shared connection to unsynchronized
statements: SQLite answered SQLITE_MISUSE ("InterfaceError: bad parameter or
other API misuse"), or a second connection appeared without the schema
("no such table", "database is locked"), and the exception escaped the tool,
the graph and the SSE stream.
"""

import threading

from app import db, sitebox, webpage_git


def _run_parallel(target, count: int) -> list[BaseException]:
    """Run ``target(index)`` in ``count`` threads; return every exception."""
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(index: int) -> None:
        try:
            target(index)
        except BaseException as exc:  # noqa: BLE001 - the test wants them all
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def _site_rows(chat_id: str) -> int:
    return db.get_conn().execute(
        "SELECT COUNT(*) AS n FROM webpage_sites WHERE chat_id = :chat_id",
        {"chat_id": chat_id},
    ).fetchone()["n"]


def test_parallel_writes_claim_one_site_and_land_every_file(isolated_db):
    from agent import tools

    user = db.create_google_user("conc@example.com", "C", "google-sub-conc")
    chat = db.create_chat(user["id"], title="conc")
    config = {
        "configurable": {
            "user_id": user["id"],
            "chat_id": chat["id"],
            "reply_id": "reply-1",
        }
    }

    def write(index: int) -> None:
        out = tools.write_webpage_file.invoke(
            {"path": f"page-{index}.html", "content": f"<h1>{index}</h1>"},
            config=config,
        )
        assert '"status": "webpage_file_written"' in out, out

    assert _run_parallel(write, 8) == []

    site = db.get_webpage_site(user["id"], chat["id"])
    assert site is not None
    assert _site_rows(chat["id"]) == 1
    written = sorted(p.name for p in sitebox.workspace_dir(site["id"]).glob("page-*.html"))
    assert written == [f"page-{i}.html" for i in range(8)]
    # One repo, initialized once: the first commit sees the files as tracked-worthy.
    assert (sitebox.workspace_dir(site["id"]) / ".git" / "HEAD").is_file()


def test_get_or_create_gives_every_thread_the_same_site(isolated_db):
    user = db.create_google_user("claim@example.com", "C", "google-sub-claim")
    chat = db.create_chat(user["id"], title="claim")
    seen: list[str] = []
    lock = threading.Lock()

    def claim(index: int) -> None:
        site = db.get_or_create_webpage_site(user["id"], chat["id"], f"key-{index}")
        with lock:
            seen.append(site["id"])

    assert _run_parallel(claim, 8) == []
    assert len(set(seen)) == 1
    assert _site_rows(chat["id"]) == 1


def test_same_insert_across_threads_does_not_misuse_the_connection(isolated_db):
    """One SQL text from many threads at once: the statement race that crashed."""
    user = db.create_google_user("hammer@example.com", "H", "google-sub-hammer")
    thread_count = 8
    chat_ids = [db.create_chat(user["id"], title="hammer")["id"] for _ in range(24)]
    slices = [chat_ids[i::thread_count] for i in range(thread_count)]

    def hammer(index: int) -> None:
        for chat_id in slices[index]:
            site = db.create_webpage_site(user["id"], chat_id, "k")
            assert db.get_webpage_site(user["id"], chat_id)["id"] == site["id"]

    assert _run_parallel(hammer, thread_count) == []
    assert sum(_site_rows(chat_id) for chat_id in chat_ids) == len(chat_ids)


def test_concurrent_repo_initialization(isolated_db):
    """`git init` copies template hooks; two at once exit 128 ("File exists")."""
    workspace = sitebox.workspace_dir("conc-repo")

    assert _run_parallel(lambda _i: webpage_git.ensure_repo(workspace), 8) == []
    assert (workspace / ".git" / "HEAD").is_file()
