"""A mutation must back up the exact admitted primary generation."""

import multiprocessing
import os
from datetime import date
from pathlib import Path

import pytest

from kptncook.repositories import RecipeInDb, RecipeRepository, RepositoryError


def replacement_attempt(base_dir, kind, operation, result):
    repo = RecipeRepository(Path(base_dir))
    sentinel = repo.base_dir / "outside"
    sentinel.write_bytes(b"outside sentinel")
    original_write = repo._write_models
    replacement_info = None

    def swapped_write(*args, **kwargs):
        nonlocal replacement_info
        if kind == "inplace":
            repo.path.write_bytes(repo.path.read_bytes().replace(b"old", b"alt"))
        else:
            repo.path.unlink(missing_ok=True)
            if kind == "symlink":
                repo.path.symlink_to(sentinel)
            elif kind == "fifo":
                os.mkfifo(repo.path)
            elif kind == "directory":
                repo.path.mkdir()
            elif kind == "generation":
                repo.path.write_bytes(b"[]")
        replacement_info = repo.path.lstat() if repo.path.exists() else None
        return original_write(*args, **kwargs)

    repo._write_models = swapped_write
    try:
        new = RecipeInDb(date=date(2026, 1, 1), data={"_id": {"$oid": "new"}})
        if operation == "add":
            repo.add(new)
        elif operation == "add_list":
            repo.add_list([new])
        else:
            repo.delete_by_ids(["old"])
    except RepositoryError:
        refused = True
    else:
        refused = False
    current = repo.path.lstat() if repo.path.exists() else None
    result.put((refused, current == replacement_info, sentinel.read_bytes()))


@pytest.mark.parametrize("operation", ["add", "add_list", "delete"])
@pytest.mark.parametrize(
    "kind", ["symlink", "fifo", "directory", "generation", "inplace", "missing"]
)
def test_replaced_primary_never_publishes_backup_or_store(tmp_path, kind, operation):
    repo = RecipeRepository(tmp_path)
    repo.add(RecipeInDb(date=date(2026, 1, 1), data={"_id": {"$oid": "old"}}))
    repo.backup_path.write_bytes(b"previous recovery")
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    process = context.Process(
        target=replacement_attempt, args=(str(tmp_path), kind, operation, result)
    )
    process.start()
    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("Backup source admission blocked on a substituted file")
    assert process.exitcode == 0
    assert result.get(timeout=2) == (True, True, b"outside sentinel")
    assert repo.backup_path.read_bytes() == b"previous recovery"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_new_store_cannot_replace_a_concurrently_created_primary(tmp_path):
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    repo = RecipeRepository(tmp_path)
    repo.backup_path.write_bytes(b"previous recovery")
    process = context.Process(
        target=replacement_attempt, args=(str(tmp_path), "generation", "add", result)
    )
    process.start()
    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("New-store mutation blocked")
    assert process.exitcode == 0
    assert result.get(timeout=2) == (True, True, b"outside sentinel")
    assert repo.path.read_bytes() == b"[]"
    assert repo.backup_path.read_bytes() == b"previous recovery"


def test_primary_replaced_while_staging_backup_preserves_recovery(
    tmp_path, monkeypatch
):
    from kptncook import repositories

    repo = RecipeRepository(tmp_path)
    repo.add(RecipeInDb(date=date(2026, 1, 1), data={"_id": {"$oid": "old"}}))
    repo.backup_path.write_bytes(b"previous recovery")
    copy = repositories.shutil.copyfileobj

    def replace_while_copying(source, target):
        repo.path.unlink()
        repo.path.write_bytes(b"[]")
        return copy(source, target)

    monkeypatch.setattr(repositories.shutil, "copyfileobj", replace_while_copying)
    with pytest.raises(RepositoryError, match="changed"):
        repo.add(RecipeInDb(date=date(2026, 1, 1), data={"_id": {"$oid": "new"}}))
    assert repo.path.read_bytes() == b"[]"
    assert repo.backup_path.read_bytes() == b"previous recovery"
    assert list(tmp_path.glob(".*.tmp")) == []


@pytest.mark.parametrize("kind", ["symlink", "oversized"])
def test_direct_backup_uses_bounded_regular_source(tmp_path, monkeypatch, kind):
    from kptncook import repositories

    repo = RecipeRepository(tmp_path)
    repo.backup_path.write_bytes(b"previous recovery")
    if kind == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(b"outside sentinel")
        repo.path.symlink_to(outside)
    else:
        monkeypatch.setattr(repositories, "MAX_STORE_BYTES", 2)
        repo.path.write_bytes(b"[] ")
    with pytest.raises(RepositoryError):
        repo.create_backup()
    assert repo.backup_path.read_bytes() == b"previous recovery"
    assert list(tmp_path.glob(".*.tmp")) == []
