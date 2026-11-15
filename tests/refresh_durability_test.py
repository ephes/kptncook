"""Durability outcomes and overlapping refreshes with synthetic local data."""

import errno
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from kptncook.repositories import RecipeInDb, RecipeRepository, RepositoryError
from kptncook.services import repository as service, workflows


def entry(oid, title):
    return RecipeInDb(
        date=date(2026, 1, 1), data={"_id": {"$oid": oid}, "title": title}
    )


@pytest.mark.parametrize("failure_at", [1, 2])
@pytest.mark.parametrize("operation", ["open", "fsync"])
def test_directory_io_failure_reports_publication_outcome(
    tmp_path, monkeypatch, failure_at, operation
):
    repo = RecipeRepository(tmp_path)
    repo.add(entry("same", "old"))
    old = repo.path.read_bytes()
    original_sync, original_open = os.fsync, os.open
    directories = []

    def fault():
        directories.append(True)
        if len(directories) == failure_at:
            raise OSError(errno.EIO, "synthetic disk fault")

    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode) and operation == "fsync":
            fault()
        return original_sync(fd)

    def open_directory(path, flags, *args, **kwargs):
        if path == tmp_path and operation == "open":
            fault()
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "open", open_directory)
    with pytest.raises(RepositoryError, match="durability.*uncertain"):
        repo.add(entry("same", "new"))
    assert repo.backup_path.read_bytes() == old
    if failure_at == 1:
        assert repo.path.read_bytes() == old
    else:
        assert repo.list() == [entry("same", "new")]
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("disjoint", [False, True])
@pytest.mark.parametrize("kind", ["today", "search", "favorites"])
def test_older_fetch_cannot_overwrite_newer_commit(
    tmp_path, monkeypatch, disjoint, kind
):
    repo = RecipeRepository(tmp_path)
    monkeypatch.setattr(service, "get_repository", lambda: repo)
    monkeypatch.setattr(workflows, "_require_access_token", lambda: None)
    older_waiting, release = threading.Event(), threading.Event()
    new_oid = "b" * 24 if disjoint else "a" * 24

    def fetch():
        if threading.current_thread().name.startswith("older"):
            older_waiting.set()
            assert release.wait(5)
            return [entry("a" * 24, "old response")]
        return [entry(new_oid, "new response")]

    class Client:
        def close(self):
            pass

        def get_by_ids(self, *args):
            return fetch()

        def list_favorites(self):
            return [{"_id": {"$oid": "a" * 24}}]

    monkeypatch.setattr(workflows, "get_today_recipes", fetch)
    monkeypatch.setattr(workflows, "_kptncook_client", Client)
    monkeypatch.setattr(
        workflows, "_resolve_recipe_summaries", lambda *args, **kwargs: fetch()
    )
    run = {
        "today": workflows.save_todays_recipes,
        "search": lambda: workflows.search_recipe_by_id("a" * 24),
        "favorites": workflows.backup_kptncook_favorites,
    }[kind]
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="older") as pool:
        pending = pool.submit(run)
        try:
            assert older_waiting.wait(5)
            run()
        finally:
            release.set()
        if disjoint:
            pending.result(timeout=5)
        else:
            with pytest.raises(workflows.UserFacingError, match="conflict"):
                pending.result(timeout=5)
    assert repo.list_by_id()[new_oid].data["title"] == "new response"
    assert len(repo.list()) == (2 if disjoint else 1)


@pytest.mark.parametrize("unsupported", [errno.EINVAL, errno.ENOTSUP, errno.ENOSYS])
def test_unsupported_directory_sync_keeps_portable_writes(
    tmp_path, monkeypatch, unsupported
):
    repo = RecipeRepository(tmp_path)
    original = os.fsync

    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(unsupported, "unsupported directory sync")
        return original(fd)

    monkeypatch.setattr(os, "fsync", sync)
    repo.add(entry("same", "first"))
    repo.add(entry("same", "next"))
    assert repo.list() == [entry("same", "next")]


@pytest.mark.parametrize("change", ["delete", "edit", "insert"])
def test_conflicting_batch_is_all_or_nothing(tmp_path, monkeypatch, change):
    repo = RecipeRepository(tmp_path)
    monkeypatch.setattr(service, "get_repository", lambda: repo)
    if change != "insert":
        repo.add(entry("same", "initial"))
    expected = repo.list_by_id()
    if change == "delete":
        repo.delete_by_ids(["same"])
    else:
        repo.add(entry("same", "competing"))
    before, backup = (
        repo.path.read_bytes(),
        repo.backup_path.read_bytes() if repo.backup_path.exists() else None,
    )
    with pytest.raises(service.RepositoryServiceError, match="conflict"):
        service.save_recipe_entries(
            [entry("same", "stale"), entry("new", "extra")], expected=expected
        )
    assert repo.path.read_bytes() == before
    assert (
        repo.backup_path.read_bytes() if repo.backup_path.exists() else None
    ) == backup


@pytest.mark.parametrize(
    "command,args,workflow",
    [
        ("dailies", [], "list_dailies_workflow"),
        ("discovery-list", ["--list-type", "latest"], "get_discovery_list_recipes"),
        ("onboarding", ["--tag", "synthetic"], "get_onboarding_recipes"),
        (
            "recipes-with-ingredients",
            ["--ingredient-id", "synthetic"],
            "get_recipes_with_ingredients",
        ),
    ],
)
def test_browsing_save_captures_before_fetch(
    tmp_path, monkeypatch, command, args, workflow
):
    from importlib import import_module
    from typer.testing import CliRunner

    cli = import_module("kptncook.cli")
    repo = RecipeRepository(tmp_path)
    repo.add(entry("same", "initial"))
    monkeypatch.setattr(service, "get_repository", lambda: repo)

    def fetch(*args, **kwargs):
        repo.add(entry("same", "newer"))
        return [entry("same", "stale")]

    monkeypatch.setattr(cli, workflow, fetch)
    result = CliRunner().invoke(cli.app, [command, *args, "--save"])
    assert result.exit_code == 1
    assert "conflict" in result.output
    assert "Added" not in result.output
    assert repo.list() == [entry("same", "newer")]
