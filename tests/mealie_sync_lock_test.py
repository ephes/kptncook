"""Same-machine sync ownership with real spawned processes and file locks."""

import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from kptncook.services import workflows


def sync_process(
    lock_dir, remote, local, started, inventory_entered, release, results, fail
):
    # Each process has independent Python state; only the synthetic server file
    # and the production advisory lock are shared.
    from kptncook.config import settings
    from kptncook.models import Recipe
    from kptncook.services.repository import RepositoryRecipesResult

    workflows.SYNC_LOCK_DIR = Path(lock_dir)
    settings.mealie_url = "https://MEALIE.example:443/api/"

    class Client:
        inventory_timeout = 120.0

        def close(self):
            pass

        def get_all_recipes(self):
            inventory_entered.set()
            rows = [json.loads(line) for line in Path(remote).read_text().splitlines()]
            if release is not None:
                assert release.wait(15)
            if fail:
                raise RuntimeError("synthetic inventory failure")
            return [SimpleNamespace(slug=row) for row in rows]

        def get_via_slug(self, slug, **kwargs):
            return SimpleNamespace(
                slug=slug, extras={"source": "kptncook", "kptncook_id": slug}
            )

        def create_recipe(self, recipe):
            with Path(remote).open("a") as output:
                output.write(json.dumps(recipe.extras["kptncook_id"]) + "\n")

    workflows.get_mealie_client = Client
    workflows.load_kptncook_recipes_from_repository = lambda: RepositoryRecipesResult(
        [Recipe.model_validate(local)], []
    )
    started.set()
    try:
        result = workflows.sync_with_mealie_result()
        results.put(("ok", result.created_count))
    except RuntimeError:
        results.put(("failure", 0))


@pytest.mark.parametrize("first_fails", [False, True])
def test_sync_inventory_and_create_have_one_owner(tmp_path, minimal, first_fails):
    context = multiprocessing.get_context("spawn")
    remote = tmp_path / "synthetic-server.jsonl"
    remote.write_text("")
    release = context.Event()
    entered = [context.Event(), context.Event()]
    started = [context.Event(), context.Event()]
    results = context.Queue()
    processes = [
        context.Process(
            target=sync_process,
            args=(
                str(tmp_path / "locks"),
                str(remote),
                minimal,
                started[i],
                entered[i],
                release if i == 0 else None,
                results,
                first_fails if i == 0 else False,
            ),
        )
        for i in range(2)
    ]
    try:
        processes[0].start()
        assert entered[0].wait(15)
        processes[1].start()
        assert started[1].wait(15)
        assert not entered[1].wait(0.5), (
            "Second process read inventory while the first owned sync"
        )
        release.set()
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
        assert sorted(results.get(timeout=3) for _ in processes) == (
            [("failure", 0), ("ok", 1)] if first_fails else [("ok", 0), ("ok", 1)]
        )
        assert [json.loads(line) for line in remote.read_text().splitlines()] == [
            minimal["_id"]["$oid"]
        ]
        assert entered[
            1
        ].is_set()  # Next owner refreshed inventory, including after failure.
    finally:
        release.set()
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)
        results.close()
        results.join_thread()


def test_sync_lock_canonical_target_and_credential_rotation(monkeypatch, tmp_path):
    from kptncook.config import settings

    monkeypatch.setattr(workflows, "SYNC_LOCK_DIR", tmp_path / "locks")
    for url, token in [
        ("https://MEALIE.example:443/api/", "first"),
        ("https://mealie.example/api", "rotated"),
    ]:
        monkeypatch.setattr(settings, "mealie_url", url)
        monkeypatch.setattr(settings, "mealie_api_token", token)
        with workflows._mealie_sync_lock():
            pass
    assert len(list((tmp_path / "locks").iterdir())) == 1
    monkeypatch.setattr(settings, "mealie_url", "https://other.example/api")
    with workflows._mealie_sync_lock():
        pass
    assert len(list((tmp_path / "locks").iterdir())) == 2


def test_sync_lock_failure_prevents_inventory_and_closes_client(monkeypatch):
    calls = []

    class Client:
        def close(self):
            calls.append("close")

        def get_all_recipes(self):
            pytest.fail("Inventory must not start without ownership")

    def fail(*args):
        raise workflows.portalocker.exceptions.LockException("synthetic")

    monkeypatch.setattr(workflows, "get_mealie_client", Client)
    monkeypatch.setattr(workflows.portalocker, "lock", fail)
    with pytest.raises(workflows.UserFacingError, match="sync lock"):
        workflows.sync_with_mealie_result()
    assert calls == ["close"]
