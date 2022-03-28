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
    data = {"_id": {"$oid": 1, "title": "test"}}
    recipe = RecipeInDb(date=date.today(), data=data)
    repo.add(recipe)

    [recipe_from_repo] = repo.list()
    assert recipe_from_repo == recipe

    repo.add(recipe)
    assert len(repo.list()) == 1


def test_add_recipe_list_to_repository(tmpdir):
    repo = RecipeRepository(tmpdir)
    data1 = {"_id": {"$oid": 1, "title": "test"}}
    recipe1 = RecipeInDb(date=date.today(), data=data1)
    data2 = {"_id": {"$oid": 2, "title": "test"}}
    recipe2 = RecipeInDb(date=date.today(), data=data2)
    recipes = [recipe1, recipe2]
    repo.add_list(recipes)
    assert len(repo.list()) == 2


def test_needs_to_be_synced(tmpdir):
    repo = RecipeRepository(tmpdir)
    today = date.today()
    assert repo.needs_to_be_synced(today)

    data = {"_id": {"$oid": 1, "title": "test"}}
    recipe = RecipeInDb(date=today, data=data)
    repo.add(recipe)
    assert not repo.needs_to_be_synced(today)
