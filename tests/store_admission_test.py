"""Synthetic store faults exercise the repository, service and CLI boundaries."""

import json
import stat
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

import kptncook.repositories as storage
from kptncook import cli
from kptncook.repositories import RecipeInDb, RecipeRepository, RepositoryError
from kptncook.services import repository as service


def entry(oid):
    return RecipeInDb(date=date(2026, 1, 1), data={"_id": {"$oid": oid}})


@pytest.mark.parametrize("operation", ["list", "add", "delete", "service", "cli"])
@pytest.mark.parametrize("payload", [b"\xff", b'[{"data":"\xff"}]'])
def test_invalid_utf8_is_private_and_preserves_snapshots(
    tmp_path, monkeypatch, operation, payload
):
    repo = RecipeRepository(tmp_path)
    repo.path.write_bytes(payload)
    repo.backup_path.write_bytes(b"recovery")
    monkeypatch.setattr(service, "get_repository", lambda: repo)
    if operation == "cli":
        result = CliRunner().invoke(cli, ["list-recipes"])
        assert result.exit_code == 1
        assert "UTF-8" in result.output
        assert "xff" not in result.output
    else:
        with pytest.raises(
            (RepositoryError, service.RepositoryServiceError), match="UTF-8"
        ):
            if operation == "list":
                repo.list()
            elif operation == "add":
                repo.add(entry("new"))
            elif operation == "delete":
                repo.delete_by_ids(["old"])
            else:
                service.list_repository_entries()
    assert repo.path.read_bytes() == payload
    assert repo.backup_path.read_bytes() == b"recovery"


@pytest.mark.parametrize("extra", [0, 1])
def test_store_exact_byte_limit(tmp_path, monkeypatch, extra):
    repo = RecipeRepository(tmp_path)
    payload = json.dumps([entry("synthetic").model_dump(mode="json")]).encode()
    monkeypatch.setattr(storage, "MAX_STORE_BYTES", len(payload), raising=False)
    repo.path.write_bytes(payload + b" " * extra)
    if extra:
        with pytest.raises(RepositoryError, match="size limit"):
            repo.list()
    else:
        assert repo.list() == [entry("synthetic")]


def test_growth_after_stat_is_bounded_and_refuses_mutation(tmp_path, monkeypatch):
    repo = RecipeRepository(tmp_path)
    repo.path.write_bytes(b"[]")
    repo.backup_path.write_bytes(b"recovery")
    monkeypatch.setattr(storage, "MAX_STORE_BYTES", 4, raising=False)
    original = storage.os.fstat
    observed = []

    def grow(fd):
        observed.append(fd)
        result = original(fd)
        with repo.path.open("ab") as target:
            target.write(b" " * 100)
        return result

    monkeypatch.setattr(storage.os, "fstat", grow)
    with pytest.raises(RepositoryError, match="size limit"):
        repo.add(entry("new"))
    assert observed
    assert repo.path.read_bytes() == b"[]" + b" " * 100
    assert repo.backup_path.read_bytes() == b"recovery"


@pytest.mark.parametrize("mode", [stat.S_IFIFO, stat.S_IFDIR, stat.S_IFSOCK])
def test_nonregular_descriptor_refused_before_read(tmp_path, monkeypatch, mode):
    repo = RecipeRepository(tmp_path)
    repo.path.write_bytes(b"[]")
    monkeypatch.setattr(
        storage.os, "fstat", lambda fd: SimpleNamespace(st_mode=mode, st_size=0)
    )
    read = Mock(side_effect=AssertionError("nonregular descriptor was read"))
    monkeypatch.setattr(storage.os, "fdopen", read)
    with pytest.raises(RepositoryError, match="regular file"):
        repo.list()
    read.assert_not_called()


def test_symlink_policy_refuses_before_open(tmp_path, monkeypatch):
    repo = RecipeRepository(tmp_path)
    repo.path.write_bytes(b"[]")
    monkeypatch.setattr(type(repo.path), "is_symlink", lambda path: True)
    open_file = Mock(side_effect=AssertionError("symlink was opened"))
    monkeypatch.setattr(storage.os, "open", open_file)
    with pytest.raises(RepositoryError, match="regular file"):
        repo.list()
    open_file.assert_not_called()


def test_os_read_failure_has_a_bounded_category(tmp_path, monkeypatch):
    repo = RecipeRepository(tmp_path)

    def fail_open(*args, **kwargs):
        raise PermissionError(13, "synthetic secret payload")

    monkeypatch.setattr(storage.os, "open", fail_open)
    with pytest.raises(RepositoryError, match="EACCES") as error:
        repo.list()
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("through_service", [False, True])
def test_repeated_delete_ids_commit_once(tmp_path, monkeypatch, through_service):
    repo = RecipeRepository(tmp_path)
    repo.add_list([entry("first"), entry("keep"), entry("last")])
    original = repo.path.read_bytes()
    write = Mock(wraps=repo._write_models)
    monkeypatch.setattr(repo, "_write_models", write)
    monkeypatch.setattr(service, "get_repository", lambda: repo)
    delete = service.delete_recipe_ids if through_service else repo.delete_by_ids
    assert delete(["last", "absent", "last", "first", "absent"]) == (
        ["last", "first"],
        ["absent"],
    )
    write.assert_called_once()
    assert repo.list() == [entry("keep")]
    assert repo.backup_path.read_bytes() == original
