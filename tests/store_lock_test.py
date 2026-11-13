"""Real filesystem and process fixtures for stable mutation lock admission."""

import multiprocessing
import os
from datetime import date
from pathlib import Path

import portalocker
import pytest

from kptncook import repositories
from kptncook.repositories import RecipeInDb, RecipeRepository, RepositoryError


def mutate(base_dir, result, waiting=None):
    if waiting is not None:
        original = repositories.portalocker.lock

        def tracked_lock(*args):
            waiting.set()
            return original(*args)

        repositories.portalocker.lock = tracked_lock
    try:
        RecipeRepository(Path(base_dir)).add(
            RecipeInDb(date=date(2026, 1, 1), data={"_id": {"$oid": "new"}})
        )
    except RepositoryError as exc:
        result.put(str(exc))
    else:
        result.put("unexpected success")


def finish(process, result):
    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("Lock admission blocked on a special file")
    assert process.exitcode == 0
    assert "lock" in result.get(timeout=2).lower()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_unusable_lock_refuses_without_mutation(tmp_path, kind):
    repo = RecipeRepository(tmp_path)
    repo.path.write_bytes(b"[]")
    repo.backup_path.write_bytes(b"previous recovery")
    sentinel = tmp_path / "outside"
    sentinel.write_bytes(b"outside sentinel")
    if kind == "symlink":
        repo.lock_path.symlink_to(sentinel)
    elif kind == "fifo":
        os.mkfifo(repo.lock_path)
    else:
        repo.lock_path.mkdir()
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    process = context.Process(target=mutate, args=(str(tmp_path), result))
    process.start()
    finish(process, result)
    assert repo.path.read_bytes() == b"[]"
    assert repo.backup_path.read_bytes() == b"previous recovery"
    assert sentinel.read_bytes() == b"outside sentinel"
    assert repo.lock_path.exists()  # Refusal must never unlink the stable name.


def test_waiting_writer_refuses_replaced_lock_inode(tmp_path):
    repo = RecipeRepository(tmp_path)
    repo.path.write_bytes(b"[]")
    repo.backup_path.write_bytes(b"previous recovery")
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    waiting = context.Event()
    process = context.Process(target=mutate, args=(str(tmp_path), result, waiting))
    try:
        with repo.lock_path.open("a+") as owner:
            portalocker.lock(owner, portalocker.LOCK_EX)
            process.start()
            assert waiting.wait(5)
            replacement = tmp_path / "replacement-lock"
            replacement.touch()
            os.replace(replacement, repo.lock_path)
            portalocker.unlock(owner)
        finish(process, result)
        assert repo.path.read_bytes() == b"[]"
        assert repo.backup_path.read_bytes() == b"previous recovery"
    finally:
        if process.pid is not None and process.is_alive():
            process.terminate()
            process.join(5)
