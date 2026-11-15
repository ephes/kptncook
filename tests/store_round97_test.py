"""Synthetic duplicate JSON and owned-process writer contention regressions."""

import multiprocessing
from datetime import date

import portalocker
import pytest

from kptncook import repositories
from kptncook.repositories import RecipeInDb, RecipeRepository, RepositoryError


def entry(oid="new"):
    return RecipeInDb(date=date(2026, 1, 1), data={"_id": {"$oid": oid}})


@pytest.mark.parametrize("operation", ["list", "add", "delete"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("level", ["record", "data", "oid"])
def test_duplicate_members_refused_privately(tmp_path, operation, reverse, level):
    first, second = ('"secret-a"', '"secret-b"')
    if reverse:
        first, second = second, first
    if level == "oid":
        data = '{"_id":{"$oid":' + first + ',"$o\\u0069d":' + second + "}}"
        record = '"data":' + data
    elif level == "data":
        data = '{"_id":{"$oid":' + first + '},"_\\u0069d":{"$oid":' + second + "}}"
        record = '"data":' + data
    else:
        record = (
            '"data":{"_id":{"$oid":'
            + first
            + '}},"d\\u0061ta":{"_id":{"$oid":'
            + second
            + "}}"
        )
    raw = ('[{"date":"2026-01-01",' + record + "}]").encode()
    repo = RecipeRepository(tmp_path)
    repo.path.write_bytes(raw)
    repo.backup_path.write_bytes(b"last good")
    with pytest.raises(RepositoryError, match="duplicate") as error:
        if operation == "list":
            repo.list()
        elif operation == "add":
            repo.add(entry())
        else:
            repo.delete_by_ids(["secret-a"])
    assert "secret" not in str(error.value)
    assert repo.path.read_bytes() == raw
    assert repo.backup_path.read_bytes() == b"last good"


def wait_for_lock(base_dir, result):
    repositories.STORE_LOCK_TIMEOUT_SECONDS = 0.1
    try:
        RecipeRepository(base_dir).add(entry())
    except RepositoryError as exc:
        result.put(str(exc))
    else:
        result.put("unexpected success")


def test_writer_deadline_preserves_store_and_later_recovers(tmp_path):
    repo = RecipeRepository(tmp_path)
    repo.path.write_bytes(b"[]")
    repo.backup_path.write_bytes(b"last good")
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    child = context.Process(target=wait_for_lock, args=(tmp_path, result))
    with repo.lock_path.open("a+") as owner:
        portalocker.lock(owner, portalocker.LOCK_EX)
        inode = repo.lock_path.stat().st_ino
        child.start()
        try:
            child.join(3)
            assert not child.is_alive(), "Writer exceeded finite lock wait budget"
            assert child.exitcode == 0
            assert "busy" in result.get(timeout=1)
            assert repo.path.read_bytes() == b"[]"
            assert repo.backup_path.read_bytes() == b"last good"
        finally:
            if child.is_alive():
                child.terminate()
                child.join(5)
    repo.add(entry())
    assert repo.list() == [entry()]
    assert repo.lock_path.stat().st_ino == inode
