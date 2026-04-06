from types import SimpleNamespace

from kptncook.models import Recipe
from kptncook.repositories import RecipeInDb
from kptncook.services import repository as repository_service
from kptncook.services.repository import InvalidStoredRecipe, RepositoryRecipesResult
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


def test_load_repository_recipes_reports_invalid_entries(monkeypatch, minimal):
    valid_entry = RecipeInDb(date=workflows.date.today(), data=minimal)
    invalid_entry = RecipeInDb(
        date=workflows.date.today(),
        data={"_id": {"$oid": "broken"}, "localizedTitle": {"de": "Broken recipe"}},
    )
    expected_recipe = Recipe.model_validate(minimal)

    monkeypatch.setattr(
        repository_service,
        "list_repository_entries",
        lambda: [valid_entry, invalid_entry],
    )

    result = repository_service.load_repository_recipes()

    assert result.recipes == [expected_recipe]
    assert len(result.invalid_entries) == 1
    assert result.invalid_entries[0].recipe_id == "broken"
    assert result.invalid_entries[0].reason == "authorComment: Field required"


def test_sync_with_mealie_continues_with_invalid_repository_entries(
    monkeypatch, minimal
):
    warning = InvalidStoredRecipe(
        position=2,
        recipe_id="broken",
        reason="steps: Field required",
    )
    recipe = Recipe.model_validate(minimal)
    created_recipe = SimpleNamespace(slug="minimal-recipe")
    mealie_recipe = SimpleNamespace(
        name="Minimal Recipe",
        extras={"kptncook_id": recipe.id.oid},
    )
    fake_client = SimpleNamespace(
        create_recipe=lambda recipe_to_create: created_recipe,
    )

    monkeypatch.setattr(workflows, "get_mealie_client", lambda: fake_client)
    monkeypatch.setattr(
        workflows,
        "get_kptncook_recipes_from_mealie",
        lambda _client: [],
    )
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(
            recipes=[recipe],
            invalid_entries=[warning],
        ),
    )
    monkeypatch.setattr(workflows, "kptncook_to_mealie", lambda _recipe: mealie_recipe)

    result = workflows.sync_with_mealie_result()

    assert result.created_count == 1
    assert result.invalid_repository_entries == [warning]


def test_export_recipes_to_paprika_accepts_parseable_repository_url(
    monkeypatch, minimal
):
    recipe = Recipe.model_validate(minimal)

    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(
            recipes=[recipe],
            invalid_entries=[],
        ),
    )
    monkeypatch.setattr(
        workflows.PaprikaExporter,
        "export",
        lambda self, recipes: "/tmp/export.paprikarecipes",
    )

    result = workflows.export_recipes_to_paprika_result(
        f"https://share.kptncook.com/{recipe.id.oid}"
    )

    assert result.filename == "/tmp/export.paprikarecipes"
    assert result.invalid_repository_entries == []


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
