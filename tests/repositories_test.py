import json
from datetime import date

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
