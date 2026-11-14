"""Special lock names must never block before the lock deadline."""

import multiprocessing
import os
from pathlib import Path

import pytest

from kptncook.services import workflows


def try_lock(directory, results):
    workflows.SYNC_LOCK_DIR = Path(directory)
    workflows.SYNC_LOCK_TIMEOUT_SECONDS = 0.1
    try:
        with workflows._mealie_sync_lock():
            results.put("entered")
    except workflows.UserFacingError:
        results.put("refused")


@pytest.mark.parametrize("kind", ["fifo", "symlink", "hardlink", "public"])
def test_unsafe_lock_name_refused_promptly(tmp_path, monkeypatch, kind):
    directory = tmp_path / "locks"
    monkeypatch.setattr(workflows, "SYNC_LOCK_DIR", directory)
    with workflows._mealie_sync_lock():
        pass
    lock = next(directory.iterdir())
    lock.unlink()
    other = tmp_path / "unrelated"
    other.write_bytes(b"Keep this synthetic content")
    other.chmod(0o600)
    if kind == "fifo":
        os.mkfifo(lock, 0o600)
    elif kind == "symlink":
        lock.symlink_to(other)
    elif kind == "hardlink":
        os.link(other, lock)
    else:
        lock.write_bytes(b"")
        lock.chmod(0o644)
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    process = ctx.Process(target=try_lock, args=(str(directory), results))
    try:
        process.start()
        process.join(5)
        assert not process.is_alive(), (
            "Opening the lock blocked before the wait deadline"
        )
        assert process.exitcode == 0
        assert results.get(timeout=1) == "refused"
        assert other.read_bytes() == b"Keep this synthetic content"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        results.close()
        results.join_thread()


def test_replaced_name_refuses_and_closes_open_descriptor(tmp_path, monkeypatch):
    monkeypatch.setattr(workflows, "SYNC_LOCK_DIR", tmp_path / "locks")
    acquire = workflows.portalocker.Lock.acquire
    opened = []

    def replace_name(lock):
        handle = acquire(lock)
        opened.append(handle)
        name = Path(handle.name)
        name.rename(name.with_suffix(".old"))
        name.touch(mode=0o600)
        return handle

    monkeypatch.setattr(workflows.portalocker.Lock, "acquire", replace_name)
    with pytest.raises(workflows.UserFacingError, match="sync lock"):
        with workflows._mealie_sync_lock():
            pytest.fail("A replaced lock name admitted sync")
    assert opened[0].closed
