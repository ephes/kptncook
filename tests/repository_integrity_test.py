"""Ambiguous identity and lock failures preserve both repository snapshots."""

import json
import multiprocessing
from datetime import date
from pathlib import Path

import pytest

from kptncook import repositories
from kptncook.repositories import RecipeInDb, RecipeRepository, RepositoryError


def entry(oid, title="Synthetic"):
    return RecipeInDb(
        date=date(2026, 11, 7), data={"_id": {"$oid": oid}, "title": title}
    )


@pytest.mark.parametrize("exact", [False, True])
@pytest.mark.parametrize("operation", ["add", "add_list", "delete", "read"])
def test_stored_duplicate_ids_preserve_files(tmp_path, exact, operation):
    repo = RecipeRepository(tmp_path)
    original = json.dumps(
        [
            entry("same").model_dump(mode="json"),
            entry("same", "Synthetic" if exact else "Different").model_dump(
                mode="json"
            ),
        ]
    ).encode()
    repo.path.write_bytes(original)
    repo.backup_path.write_bytes(b"previous recovery")
    with pytest.raises(RepositoryError, match="record 2.*duplicate.*record 1") as error:
        if operation == "add":
            repo.add(entry("new"))
        elif operation == "add_list":
            repo.add_list([entry("new")])
        elif operation == "delete":
            repo.delete_by_ids(["same"])
        else:
            repo.list_by_id()
    assert "Different" not in str(error.value)
    assert repo.path.read_bytes() == original
    assert repo.backup_path.read_bytes() == b"previous recovery"


@pytest.mark.parametrize("exact", [False, True])
def test_duplicate_incoming_batch_preserves_files(tmp_path, exact):
    repo = RecipeRepository(tmp_path)
    repo.path.write_bytes(b"[]")
    repo.backup_path.write_bytes(b"previous recovery")
    with pytest.raises(RepositoryError, match="duplicate"):
        repo.add_list(
            [entry("same"), entry("same", "Synthetic" if exact else "Different")]
        )
    assert repo.path.read_bytes() == b"[]"
    assert repo.backup_path.read_bytes() == b"previous recovery"
    assert not repo.lock_path.exists()


@pytest.mark.parametrize("operation", ["add", "add_list", "delete"])
def test_lock_failure_refuses_mutation_but_keeps_reads(
    tmp_path, monkeypatch, operation
):
    repo = RecipeRepository(tmp_path)
    original = json.dumps([entry("stored").model_dump(mode="json")]).encode()
    repo.path.write_bytes(original)
    repo.backup_path.write_bytes(b"previous recovery")

    def fail_lock(*args):
        raise repositories.portalocker.exceptions.LockException(
            "synthetic unavailable locking"
        )

    monkeypatch.setattr(repositories.portalocker, "lock", fail_lock)
    with pytest.raises(RepositoryError, match="lock"):
        if operation == "add":
            repo.add(entry("new"))
        elif operation == "add_list":
            repo.add_list([entry("new")])
        else:
            repo.delete_by_ids(["stored"])
    assert repo.path.read_bytes() == original
    assert repo.backup_path.read_bytes() == b"previous recovery"
    assert repo.list_by_id() == {"stored": entry("stored")}


def writer(base_dir, oid, ready, read, release):
    # Independent processes exercise the portable backend on this platform.
    repo = RecipeRepository(Path(base_dir))
    if release is not None:
        original = repo._write_models

        def gated_write(models, **kwargs):
            read.set()
            assert release.wait(15)
            original(models, **kwargs)

        repo._write_models = gated_write
    else:
        original_read = repo._read_models

        def tracked_read():
            result = original_read()
            read.set()
            return result

        repo._read_models = tracked_read
    ready.set()
    repo.add(entry(oid))


def test_process_writers_keep_independent_updates(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready = [context.Event(), context.Event()]
    read = [context.Event(), context.Event()]
    release = context.Event()
    processes = [
        context.Process(
            target=writer,
            args=(
                str(tmp_path),
                str(i),
                ready[i],
                read[i],
                release if i == 0 else None,
            ),
        )
        for i in range(2)
    ]
    try:
        processes[0].start()
        assert read[0].wait(15)
        processes[1].start()
        assert ready[1].wait(15)
        assert not read[1].wait(0.5), "Concurrent writer read a stale repository"
        release.set()
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
        assert set(RecipeRepository(tmp_path).list_by_id()) == {"0", "1"}
    finally:
        release.set()
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)


@pytest.mark.parametrize("feed", ["today", "dailies"])
def test_duplicated_feed_is_refused_at_save_boundary(
    monkeypatch, tmp_path, minimal, feed
):
    import httpx
    from kptncook.api import KptnCookClient
    from kptncook.services import repository as service

    repo = RecipeRepository(tmp_path)
    repo.path.write_bytes(b"[]")
    repo.backup_path.write_bytes(b"previous recovery")
    monkeypatch.setattr(service, "get_repository", lambda: repo)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=[minimal, minimal])
        )
    ) as http:
        client = KptnCookClient(client=http)
        entries = client.list_today() if feed == "today" else client.list_dailies()
    assert len(entries) == 2
    with pytest.raises(
        service.RepositoryServiceError, match="Incoming record 2.*duplicate.*record 1"
    ) as error:
        service.save_recipe_entries(entries)
    assert "repair duplicates" not in str(error.value)
    assert repo.path.read_bytes() == b"[]"
    assert repo.backup_path.read_bytes() == b"previous recovery"
