import copy
import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from kptncook.models import Recipe
from kptncook.repositories import RecipeInDb
from kptncook.services import repository as repository_service
from kptncook.services import workflows
from kptncook.services.repository import InvalidStoredRecipe, RepositoryRecipesResult


@pytest.mark.parametrize("status", [200, 401])
@pytest.mark.parametrize("warning_fails", [False, True])
def test_login_close_failure_preserves_exchange_outcome(
    monkeypatch, caplog, status, warning_fails
):
    token = "synthetic-token"

    def respond(request):
        assert request.url.path == "/auth/login"
        return httpx.Response(status, json={"accessToken": token})

    http = httpx.Client(transport=httpx.MockTransport(respond))
    real_close = http.close

    def fail_close():
        real_close()
        raise OSError(f"private close detail: {token}")

    close = Mock(side_effect=fail_close)
    monkeypatch.setattr(http, "close", close)
    monkeypatch.setattr("kptncook.http_client.httpx.Client", lambda **kwargs: http)
    monkeypatch.setattr(
        workflows,
        "get_credentials",
        lambda **kwargs: ("synthetic-user", "synthetic-pw"),
    )
    if warning_fails:
        warning = Mock(side_effect=RuntimeError("warning handler failed"))
        monkeypatch.setattr(workflows.logger, "warning", warning)

    if status == 200:
        assert workflows.get_kptncook_access_token() == token
    else:
        with pytest.raises(
            workflows.UserFacingError, match=r"Login failed \(HTTP 401\)"
        ):
            workflows.get_kptncook_access_token()

    close.assert_called_once_with()
    if warning_fails:
        warning.assert_called_once_with("KptnCook client cleanup failed.")
    else:
        assert "KptnCook client cleanup failed." in caplog.text
        assert token not in caplog.text
        assert "private close detail" not in caplog.text


def _recipe_data(minimal, *, oid: str | None = None) -> dict:
    data = copy.deepcopy(minimal)
    if oid is not None:
        data["_id"]["$oid"] = oid
    return data


def _recipe(minimal, *, oid: str | None = None) -> Recipe:
    return Recipe.model_validate(_recipe_data(minimal, oid=oid))


def _recipe_in_db(minimal, *, oid: str | None = None) -> RecipeInDb:
    return RecipeInDb(date=workflows.date.today(), data=_recipe_data(minimal, oid=oid))


def _status_error(
    status_code: int, *, detail_message: str | None = None
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mealie.example.com/api/recipes")
    response_kwargs = {"request": request}
    if detail_message is not None:
        response_kwargs["json"] = {"detail": {"message": detail_message}}
    response = httpx.Response(status_code, **response_kwargs)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_save_todays_recipes_uses_repository_service(monkeypatch):
    recipe = RecipeInDb(
        date=workflows.date.today(),
        data={"_id": {"$oid": "635a68635100007500061cd7"}},
    )
    captured = {}

    monkeypatch.setattr(workflows, "repository_needs_sync", lambda _date: True)
    monkeypatch.setattr(workflows, "get_today_recipes", lambda: [recipe])

    def fake_save_recipe_entries(recipes, *, expected):
        assert expected == {}
        captured["recipes"] = recipes
        return len(recipes)

    monkeypatch.setattr(workflows, "save_recipe_entries", fake_save_recipe_entries)

    assert workflows.save_todays_recipes() == 1
    assert captured["recipes"] == [recipe]


def test_save_todays_recipes_skips_fetch_when_repository_is_current(monkeypatch):
    monkeypatch.setattr(workflows, "repository_needs_sync", lambda _date: False)
    monkeypatch.setattr(
        workflows,
        "get_today_recipes",
        lambda: pytest.fail("today recipes should not be fetched"),
    )
    monkeypatch.setattr(
        workflows,
        "save_recipe_entries",
        lambda recipes: pytest.fail(f"repository should not be written: {recipes}"),
    )

    assert workflows.save_todays_recipes() == 0


def test_save_todays_recipes_wraps_repository_errors(monkeypatch):
    def fail_needs_sync(_date):
        raise repository_service.RepositoryServiceError("repository unavailable")

    monkeypatch.setattr(workflows, "repository_needs_sync", fail_needs_sync)

    with pytest.raises(workflows.UserFacingError, match="repository unavailable"):
        workflows.save_todays_recipes()


def test_load_repository_recipes_reports_invalid_entries(monkeypatch, minimal):
    valid_entry = RecipeInDb(date=workflows.date.today(), data=minimal)
    invalid_data = _recipe_data(minimal, oid="broken")
    invalid_data.pop("steps")
    invalid_entry = RecipeInDb(
        date=workflows.date.today(),
        data=invalid_data,
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
    assert result.invalid_entries[0].reason == "steps: Field required"


def test_sync_with_mealie_reports_name_collisions_and_other_failures(
    monkeypatch, minimal, caplog
):
    warning = InvalidStoredRecipe(
        position=2,
        recipe_id="broken",
        reason="steps: Field required",
    )
    repository_recipes = [
        _recipe(minimal, oid="recipe-1"),
        _recipe(minimal, oid="recipe-2"),
        _recipe(minimal, oid="recipe-3"),
    ]
    mealie_recipes = [
        SimpleNamespace(
            name=f"Minimal Recipe {index}",
            extras={"kptncook_id": recipe.id.oid},
        )
        for index, recipe in enumerate(repository_recipes, start=1)
    ]
    seen_ids: list[str] = []

    class FakeClient:
        def close(self):
            pass

        def create_recipe(self, recipe_to_create):
            recipe_id = recipe_to_create.extras["kptncook_id"]
            seen_ids.append(recipe_id)
            if recipe_id == "recipe-1":
                raise _status_error(409, detail_message="Recipe already exists")
            if recipe_id == "recipe-2":
                raise _status_error(500, detail_message="upstream exploded")
            return SimpleNamespace(slug=f"created-{recipe_id}")

    fake_client = FakeClient()

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
            recipes=repository_recipes,
            invalid_entries=[warning],
        ),
    )
    monkeypatch.setattr(
        workflows,
        "kptncook_to_mealie",
        lambda recipe: mealie_recipes[repository_recipes.index(recipe)],
    )

    with caplog.at_level(logging.WARNING):
        result = workflows.sync_with_mealie_result()

    assert result.created_count == 1
    assert result.invalid_repository_entries == [warning]
    assert seen_ids == ["recipe-1", "recipe-2", "recipe-3"]
    assert "Minimal Recipe" in caplog.text
    assert "upstream exploded" not in caplog.text
    assert "Recipe already exists" in caplog.text
    assert len(result.failed_recipes) == 2
    assert result.failed_recipes[0].recipe_id == "recipe-1"
    assert result.failed_recipes[0].recipe_name == "Minimal Recipe"
    assert "HTTP 409" in result.failed_recipes[0].reason
    assert "Recipe already exists" in result.failed_recipes[0].reason
    assert "HTTP 500" in result.failed_recipes[1].reason
    assert "upstream exploded" not in result.failed_recipes[1].reason


def _patch_sync_sources(monkeypatch, minimal, fake_client, count: int) -> None:
    repository_recipes = [
        _recipe(minimal, oid=f"recipe-{index}") for index in range(1, count + 1)
    ]
    mealie_recipes = [
        SimpleNamespace(
            name=f"Minimal Recipe {index}",
            extras={"kptncook_id": recipe.id.oid},
        )
        for index, recipe in enumerate(repository_recipes, start=1)
    ]
    monkeypatch.setattr(workflows, "get_mealie_client", lambda: fake_client)
    monkeypatch.setattr(
        workflows, "get_kptncook_recipes_from_mealie", lambda _client: []
    )
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(recipes=repository_recipes, invalid_entries=[]),
    )
    monkeypatch.setattr(
        workflows,
        "kptncook_to_mealie",
        lambda recipe: mealie_recipes[repository_recipes.index(recipe)],
    )


def test_sync_with_mealie_continues_after_connection_and_unexpected_errors(
    monkeypatch, minimal
):
    seen_ids: list[str] = []

    class FakeClient:
        def close(self):
            pass

        def create_recipe(self, recipe_to_create):
            recipe_id = recipe_to_create.extras["kptncook_id"]
            seen_ids.append(recipe_id)
            if recipe_id == "recipe-1":
                raise httpx.ConnectError("connection refused")
            if recipe_id == "recipe-2":
                raise ValueError("unexpected payload")
            return SimpleNamespace(slug=f"created-{recipe_id}")

    _patch_sync_sources(monkeypatch, minimal, FakeClient(), 3)

    result = workflows.sync_with_mealie_result()

    assert seen_ids == ["recipe-1", "recipe-2", "recipe-3"]
    assert result.created_count == 1
    assert len(result.failed_recipes) == 2
    assert result.failed_recipes[0].recipe_name == "Minimal Recipe"
    assert "Request failed: connection error" in result.failed_recipes[0].reason
    assert "outcome is unknown" in result.failed_recipes[0].reason
    assert result.failed_recipes[1] == workflows.MealieSyncFailure(
        "recipe-2", "Minimal Recipe", "Local processing error (ValueError)"
    )


def test_sync_with_mealie_reports_name_clash_with_other_kptncook_recipe(
    monkeypatch, minimal
):
    # Same title is not the same identity. A server-side collision remains a
    # visible failure, including when the existing recipe is from KptnCook.
    class FakeClient:
        def close(self):
            pass

        def create_recipe(self, recipe_to_create):
            raise _status_error(400, detail_message="Recipe already exists")

    _patch_sync_sources(monkeypatch, minimal, FakeClient(), 1)
    monkeypatch.setattr(
        workflows,
        "get_kptncook_recipes_from_mealie",
        lambda _client: [
            SimpleNamespace(
                name="Minimal Recipe 1",
                extras={"source": "kptncook", "kptncook_id": "other-id"},
            )
        ],
    )

    result = workflows.sync_with_mealie_result()

    assert len(result.failed_recipes) == 1
    assert result.failed_recipes[0].recipe_name == "Minimal Recipe"
    assert "Recipe already exists" in result.failed_recipes[0].reason
    assert result.failed_recipes[0].recipe_id == "recipe-1"


def test_sync_with_mealie_prefilters_recipes_already_present_in_mealie(
    monkeypatch, minimal
):
    repository_recipes = [
        _recipe(minimal, oid="recipe-1"),
        _recipe(minimal, oid="recipe-2"),
    ]
    mealie_recipes = [
        SimpleNamespace(
            name=f"Minimal Recipe {index}",
            extras={"kptncook_id": recipe.id.oid},
        )
        for index, recipe in enumerate(repository_recipes, start=1)
    ]
    seen_ids: list[str] = []

    class FakeClient:
        def close(self):
            pass

        def create_recipe(self, recipe_to_create):
            recipe_id = recipe_to_create.extras["kptncook_id"]
            seen_ids.append(recipe_id)
            return SimpleNamespace(slug=f"created-{recipe_id}")

    fake_client = FakeClient()

    monkeypatch.setattr(workflows, "get_mealie_client", lambda: fake_client)
    monkeypatch.setattr(
        workflows,
        "get_kptncook_recipes_from_mealie",
        lambda _client: [mealie_recipes[0]],
    )
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(
            recipes=repository_recipes,
            invalid_entries=[],
        ),
    )
    monkeypatch.setattr(
        workflows,
        "kptncook_to_mealie",
        lambda recipe: mealie_recipes[repository_recipes.index(recipe)],
    )

    result = workflows.sync_with_mealie_result()

    assert result.created_count == 1
    assert result.invalid_repository_entries == []
    assert seen_ids == ["recipe-2"]
    assert result.failed_recipes == []


def test_sync_result_defaults_do_not_share_failure_lists():
    first = workflows.SyncWithMealieResult(0, [])
    second = workflows.SyncWithMealieResult(0, [])

    assert first.failed_recipes == []
    assert first.failed_recipes is not second.failed_recipes


@pytest.mark.parametrize(
    "error, expected_reason",
    [
        (httpx.ReadTimeout("response timed out"), "ReadTimeout"),
        (httpx.ConnectError("connection refused"), "connection error"),
        (httpx.HTTPError("transport failed"), "HTTP error"),
        (ValueError("invalid recipe payload"), "Local processing error (ValueError)"),
        (TypeError("not JSON serializable"), "Local processing error (TypeError)"),
        (
            ValidationError.from_exception_data(
                "MealieRecipe", [{"type": "missing", "loc": ("name",), "input": {}}]
            ),
            "Local processing error (ValidationError)",
        ),
        (
            PydanticSerializationError("unsupported field type"),
            "Local processing error (PydanticSerializationError)",
        ),
    ],
)
def test_sync_with_mealie_continues_after_create_failure(
    monkeypatch, minimal, error, expected_reason
):
    recipes = [_recipe(minimal, oid=f"recipe-{index}") for index in (1, 2)]
    created_ids = []

    class FakeClient:
        def close(self):
            pass

        def create_recipe(self, recipe):
            created_ids.append(recipe.extras["kptncook_id"])
            if len(created_ids) == 1:
                raise error
            return SimpleNamespace(slug="created-recipe-2")

    monkeypatch.setattr(workflows, "get_mealie_client", FakeClient)
    monkeypatch.setattr(workflows, "get_kptncook_recipes_from_mealie", lambda _: [])
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(recipes, []),
    )
    result = workflows.sync_with_mealie_result()

    assert result.created_count == 1
    assert created_ids == ["recipe-1", "recipe-2"]  # No retry or deletion.
    assert len(result.failed_recipes) == 1
    failure = result.failed_recipes[0]
    assert failure.recipe_id == "recipe-1"
    assert failure.recipe_name == "Minimal Recipe"
    assert expected_reason in failure.reason
    if isinstance(error, httpx.HTTPError):
        assert "next sync" in failure.reason.lower()
        assert "stored identity" in failure.reason.lower()
    if isinstance(error, httpx.TimeoutException):
        assert "unknown" in failure.reason.lower()


def test_sync_with_mealie_continues_without_exposing_conversion_error(
    monkeypatch, minimal, caplog
):
    recipes = [_recipe(minimal, oid=f"recipe-{index}") for index in (1, 2)]
    real_convert = workflows.kptncook_to_mealie
    sentinel = "synthetic-private-recipe-value"
    created_ids = []

    def convert(recipe):
        if recipe.id.oid == "recipe-1":
            Recipe.model_validate({**minimal, "preparationTime": sentinel})
        return real_convert(recipe)

    class FakeClient:
        def close(self):
            pass

        def create_recipe(self, recipe):
            created_ids.append(recipe.extras["kptncook_id"])
            return SimpleNamespace(slug="created-recipe-2")

    monkeypatch.setattr(workflows, "get_mealie_client", FakeClient)
    monkeypatch.setattr(workflows, "get_kptncook_recipes_from_mealie", lambda _: [])
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(recipes, []),
    )
    monkeypatch.setattr(workflows, "kptncook_to_mealie", convert)

    result = workflows.sync_with_mealie_result()

    assert result.created_count == 1
    assert created_ids == ["recipe-2"]
    [failure] = result.failed_recipes
    assert sentinel not in failure.reason
    assert sentinel not in caplog.text
    assert failure == workflows.MealieSyncFailure(
        "recipe-1", "Minimal Recipe", "Local processing error (ValidationError)"
    )
    assert "Minimal Recipe (recipe-1)" in caplog.text
    assert failure.reason in caplog.text


def test_sync_with_mealie_count_wrapper_does_not_hide_failures(monkeypatch):
    monkeypatch.setattr(
        workflows,
        "sync_with_mealie_result",
        lambda: workflows.SyncWithMealieResult(
            created_count=1,
            invalid_repository_entries=[],
            failed_recipes=[workflows.MealieSyncFailure("recipe-1", "Soup", "bad")],
        ),
    )

    with pytest.raises(workflows.UserFacingError, match="Soup.*recipe-1.*bad"):
        workflows.sync_with_mealie()


def test_backup_kptncook_favorites_resolves_and_saves_recipes(monkeypatch, minimal):
    expected_recipes = [
        _recipe_in_db(minimal, oid="favorite-1"),
        _recipe_in_db(minimal, oid="favorite-2"),
    ]
    favorites = [{"id": "favorite-1"}, {"id": "favorite-2"}, {"id": "favorite-3"}]
    captured: dict[str, object] = {}

    class FakeClient:
        def close(self):
            pass

        def list_favorites(self):
            return favorites

    fake_client = FakeClient()

    monkeypatch.setattr(
        workflows,
        "_require_access_token",
        lambda: captured.setdefault("required", True),
    )
    monkeypatch.setattr(workflows, "KptnCookClient", lambda: fake_client)
    monkeypatch.setattr(
        workflows,
        "_collect_recipe_identifiers",
        lambda items: [("oid", item["id"]) for item in items[:2]],
    )

    def fake_resolve_recipe_summaries(client, items, *, action):
        captured["client"] = client
        captured["items"] = items
        captured["action"] = action
        return expected_recipes

    monkeypatch.setattr(
        workflows, "_resolve_recipe_summaries", fake_resolve_recipe_summaries
    )
    monkeypatch.setattr(
        workflows,
        "_save_repository_entries",
        lambda recipes, *, expected: len(recipes),
    )

    result = workflows.backup_kptncook_favorites()

    assert result == workflows.FavoritesBackupResult(favorite_count=3, saved_count=2)
    assert captured["required"] is True
    assert captured["client"] is fake_client
    assert captured["items"] == [("oid", "favorite-1"), ("oid", "favorite-2")]
    assert captured["action"] == "resolving recipes"


def test_get_discovery_list_recipes_resolves_list_items(monkeypatch, minimal):
    expected_recipes = [_recipe_in_db(minimal, oid="discovery-1")]
    captured: dict[str, object] = {}

    class FakeClient:
        def close(self):
            pass

        def get_discovery_list(self, *, list_type, list_id):
            captured["list_type"] = list_type
            captured["list_id"] = list_id
            return [{"id": "discovery-1"}]

    fake_client = FakeClient()
    monkeypatch.setattr(workflows, "KptnCookClient", lambda: fake_client)

    def fake_resolve_recipe_summaries(client, items, *, action):
        captured["client"] = client
        captured["items"] = items
        captured["action"] = action
        return expected_recipes

    monkeypatch.setattr(
        workflows, "_resolve_recipe_summaries", fake_resolve_recipe_summaries
    )

    result = workflows.get_discovery_list_recipes(list_type="curated", list_id="abc123")

    assert result == expected_recipes
    assert captured["list_type"] == "curated"
    assert captured["list_id"] == "abc123"
    assert captured["client"] is fake_client
    assert captured["items"] == [{"id": "discovery-1"}]
    assert captured["action"] == "resolving recipes"


def test_get_recipes_with_ingredients_requires_access_token_and_resolves_items(
    monkeypatch, minimal
):
    expected_recipes = [_recipe_in_db(minimal, oid="ingredient-1")]
    captured: dict[str, object] = {}

    class FakeClient:
        def close(self):
            pass

        def get_recipes_with_ingredients(self, *, ingredient_ids):
            captured["ingredient_ids"] = ingredient_ids
            return [{"id": "ingredient-1"}]

    fake_client = FakeClient()
    monkeypatch.setattr(
        workflows,
        "_require_access_token",
        lambda: captured.setdefault("required", True),
    )
    monkeypatch.setattr(workflows, "KptnCookClient", lambda: fake_client)

    def fake_resolve_recipe_summaries(client, items, *, action):
        captured["client"] = client
        captured["items"] = items
        captured["action"] = action
        return expected_recipes

    monkeypatch.setattr(
        workflows, "_resolve_recipe_summaries", fake_resolve_recipe_summaries
    )

    result = workflows.get_recipes_with_ingredients(["123", "456"])

    assert result == expected_recipes
    assert captured["required"] is True
    assert captured["ingredient_ids"] == ["123", "456"]
    assert captured["client"] is fake_client
    assert captured["items"] == [{"id": "ingredient-1"}]
    assert captured["action"] == "resolving recipes"


def test_get_onboarding_recipes_resolves_items(monkeypatch, minimal):
    expected_recipes = [_recipe_in_db(minimal, oid="onboarding-1")]
    captured: dict[str, object] = {}

    class FakeClient:
        def close(self):
            pass

        def get_onboarding_recipes(self, *, tags):
            captured["tags"] = tags
            return [{"id": "onboarding-1"}]

    fake_client = FakeClient()
    monkeypatch.setattr(workflows, "KptnCookClient", lambda: fake_client)

    def fake_resolve_recipe_summaries(client, items, *, action):
        captured["client"] = client
        captured["items"] = items
        captured["action"] = action
        return expected_recipes

    monkeypatch.setattr(
        workflows, "_resolve_recipe_summaries", fake_resolve_recipe_summaries
    )

    result = workflows.get_onboarding_recipes(["rt:diet_vegetarian"])

    assert result == expected_recipes
    assert captured["tags"] == ["rt:diet_vegetarian"]
    assert captured["client"] is fake_client
    assert captured["items"] == [{"id": "onboarding-1"}]
    assert captured["action"] == "resolving recipes"


def test_delete_recipes_by_selection_deduplicates_ids_and_reports_missing(
    monkeypatch, minimal
):
    recipes = [
        _recipe(minimal, oid="recipe-1"),
        _recipe(minimal, oid="recipe-2"),
    ]
    entries = {
        "recipe-1": _recipe_in_db(minimal, oid="recipe-1"),
        "recipe-2": _recipe_in_db(minimal, oid="recipe-2"),
        "broken": RecipeInDb(date=date.today(), data={"_id": {"$oid": "broken"}}),
    }
    monkeypatch.setattr(
        workflows,
        "_repository_id_map",
        lambda: entries,
    )

    result = workflows.delete_recipes_by_selection(
        indices=[0, -1, 3],
        oids=["recipe-1", "missing"],
    )

    assert result.recipes == recipes
    assert result.invalid_indices == [-1, 3]
    assert result.missing_ids == ["missing"]
    assert result.to_delete_ids == ["recipe-1"]
    assert result.expected == {"recipe-1": entries["recipe-1"]}
    assert len(result.invalid_repository_entries) == 1
    assert result.invalid_repository_entries[0].recipe_id == "broken"


def test_export_recipes_to_paprika_accepts_parseable_repository_url(
    monkeypatch, minimal
):
    recipe = Recipe.model_validate(minimal)

    monkeypatch.setattr(
        repository_service,
        "list_repository_entries",
        lambda: [RecipeInDb(date=date.today(), data=minimal)],
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
