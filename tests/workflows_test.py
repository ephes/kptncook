from kptncook.repositories import RecipeInDb
from kptncook.services import workflows


def test_save_todays_recipes_uses_repository_service(monkeypatch):
    recipe = RecipeInDb(
        date=workflows.date.today(),
        data={"_id": {"$oid": "635a68635100007500061cd7"}},
    )
    captured = {}

    monkeypatch.setattr(workflows, "repository_needs_sync", lambda _date: True)
    monkeypatch.setattr(workflows, "get_today_recipes", lambda: [recipe])

    def fake_save_recipe_entries(recipes):
        captured["recipes"] = recipes
        return len(recipes)

    monkeypatch.setattr(workflows, "save_recipe_entries", fake_save_recipe_entries)

    assert workflows.save_todays_recipes() == 1
    assert captured["recipes"] == [recipe]


def test_get_discovery_screen_returns_structured_entries(monkeypatch):
    monkeypatch.setattr(
        workflows.KptnCookClient,
        "get_discovery_screen",
        lambda self: {
            "lists": [
                {
                    "id": "abc123",
                    "title": {"de": "Trending"},
                    "listType": "curated",
                }
            ],
            "quickSearchEntries": [{"title": "Winter"}],
        },
    )

    data = workflows.get_discovery_screen()

    assert len(data.lists) == 1
    assert data.lists[0].list_id == "abc123"
    assert data.lists[0].title == "Trending"
    assert data.lists[0].list_type == "curated"
    assert data.quick_search == ["Winter"]
