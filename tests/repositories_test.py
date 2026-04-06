import json
from datetime import date

import pytest

import kptncook.repositories as repositories_module
from kptncook.repositories import RecipeInDb, RecipeRepository


def test_no_repository_file_returns_empty_list(tmpdir):
    repo = RecipeRepository(tmpdir)
    assert repo.list() == []


def test_entries_from_json_show_up_in_list(tmpdir):
    today = date.today()
    repo = RecipeRepository(tmpdir)
    data = [
        {"date": str(today), "data": {"title": "test"}},
    ]
    with repo.path.open("w") as f:
        f.write(json.dumps(data))

    [recipe] = repo.list()
    assert recipe.date == today
    assert recipe.data == {"title": "test"}


def test_add_recipe_to_repository(tmpdir):
    repo = RecipeRepository(tmpdir)
    data = {"_id": {"$oid": "1", "title": "test"}}
    recipe = RecipeInDb(date=date.today(), data=data)
    repo.add(recipe)

    [recipe_from_repo] = repo.list()
    assert recipe_from_repo == recipe

    repo.add(recipe)
    assert len(repo.list()) == 1


def test_add_recipe_list_to_repository(tmpdir):
    repo = RecipeRepository(tmpdir)
    data1 = {"_id": {"$oid": "1", "title": "test"}}
    recipe1 = RecipeInDb(date=date.today(), data=data1)
    data2 = {"_id": {"$oid": "2", "title": "test"}}
    recipe2 = RecipeInDb(date=date.today(), data=data2)
    recipes = [recipe1, recipe2]
    repo.add_list(recipes)
    assert len(repo.list()) == 2


def test_needs_to_be_synced(tmpdir):
    repo = RecipeRepository(tmpdir)
    today = date.today()
    assert repo.needs_to_be_synced(today)

    data = {"_id": {"$oid": "1", "title": "test"}}
    recipe = RecipeInDb(date=today, data=data)
    repo.add(recipe)
    assert not repo.needs_to_be_synced(today)


def test_delete_recipe_by_id(tmpdir):
    repo = RecipeRepository(tmpdir)
    data1 = {"_id": {"$oid": "1", "title": "test"}}
    recipe1 = RecipeInDb(date=date.today(), data=data1)
    data2 = {"_id": {"$oid": "2", "title": "test"}}
    recipe2 = RecipeInDb(date=date.today(), data=data2)
    repo.add_list([recipe1, recipe2])

    deleted, missing = repo.delete_by_ids(["1"])
    assert deleted == ["1"]
    assert missing == []
    remaining_ids = [str(recipe.id) for recipe in repo.list()]
    assert remaining_ids == ["2"]


def test_delete_multiple_recipes_by_id(tmpdir):
    repo = RecipeRepository(tmpdir)
    data1 = {"_id": {"$oid": "1", "title": "test"}}
    recipe1 = RecipeInDb(date=date.today(), data=data1)
    data2 = {"_id": {"$oid": "2", "title": "test"}}
    recipe2 = RecipeInDb(date=date.today(), data=data2)
    data3 = {"_id": {"$oid": "3", "title": "test"}}
    recipe3 = RecipeInDb(date=date.today(), data=data3)
    repo.add_list([recipe1, recipe2, recipe3])

    deleted, missing = repo.delete_by_ids(["1", "3"])
    assert deleted == ["1", "3"]
    assert missing == []
    remaining_ids = [str(recipe.id) for recipe in repo.list()]
    assert remaining_ids == ["2"]


def test_delete_recipe_by_id_reports_missing(tmpdir):
    repo = RecipeRepository(tmpdir)
    data = {"_id": {"$oid": "abc", "title": "test"}}
    recipe = RecipeInDb(date=date.today(), data=data)
    repo.add(recipe)

    deleted, missing = repo.delete_by_ids(["missing"])
    assert deleted == []
    assert missing == ["missing"]
    assert len(repo.list()) == 1


def test_add_recipe_uses_atomic_replace(tmpdir, monkeypatch):
    repo = RecipeRepository(tmpdir)
    data = {"_id": {"$oid": "1", "title": "test"}}
    recipe = RecipeInDb(date=date.today(), data=data)
    calls: list[tuple[str, str]] = []
    original_replace = repositories_module.os.replace

    def tracking_replace(src, dst):
        calls.append((str(src), str(dst)))
        return original_replace(src, dst)

    monkeypatch.setattr(repositories_module.os, "replace", tracking_replace)

    repo.add(recipe)

    assert len(calls) == 1
    src, dst = calls[0]
    assert src != str(repo.path)
    assert dst == str(repo.path)
    assert list(repo.path.parent.glob(f".{repo.path.name}.*.tmp")) == []


def test_failed_atomic_replace_keeps_existing_repository(tmpdir, monkeypatch):
    repo = RecipeRepository(tmpdir)
    recipe1 = RecipeInDb(
        date=date.today(), data={"_id": {"$oid": "1", "title": "first"}}
    )
    recipe2 = RecipeInDb(
        date=date.today(), data={"_id": {"$oid": "2", "title": "second"}}
    )
    repo.add(recipe1)
    original_content = repo.path.read_text(encoding="utf-8")
    original_replace = repositories_module.os.replace

    def failing_replace(src, dst):
        if str(dst) == str(repo.path):
            raise OSError("replace failed")
        return original_replace(src, dst)

    monkeypatch.setattr(repositories_module.os, "replace", failing_replace)

    with pytest.raises(repositories_module.RepositoryError, match="Could not write"):
        repo.add(recipe2)

    assert repo.path.read_text(encoding="utf-8") == original_content
    assert repo.backup_path.exists()
    assert repo.backup_path.read_text(encoding="utf-8") == original_content
    assert list(repo.path.parent.glob(f".{repo.path.name}.*.tmp")) == []


def test_backup_contains_previous_repository_state(tmpdir):
    repo = RecipeRepository(tmpdir)
    recipe1 = RecipeInDb(
        date=date.today(), data={"_id": {"$oid": "1", "title": "first"}}
    )
    recipe2 = RecipeInDb(
        date=date.today(), data={"_id": {"$oid": "2", "title": "second"}}
    )
    repo.add(recipe1)
    repo.add(recipe2)

    backup_data = json.loads(repo.backup_path.read_text(encoding="utf-8"))

    assert [entry["data"]["_id"]["$oid"] for entry in backup_data] == ["1"]


@pytest.mark.skipif(
    repositories_module.fcntl is None,
    reason="fcntl locking is only available on POSIX platforms",
)
def test_add_recipe_uses_advisory_lock(tmpdir, monkeypatch):
    repo = RecipeRepository(tmpdir)
    recipe = RecipeInDb(date=date.today(), data={"_id": {"$oid": "1"}})
    calls: list[int] = []
    original_flock = repositories_module.fcntl.flock

    def tracking_flock(fd, operation):
        calls.append(operation)
        return original_flock(fd, operation)

    monkeypatch.setattr(repositories_module.fcntl, "flock", tracking_flock)

    repo.add(recipe)

    assert repositories_module.fcntl.LOCK_EX in calls
    assert repositories_module.fcntl.LOCK_UN in calls
