"""Numeric model admission and real repository/export boundary contracts."""

import copy
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from kptncook.exporter_utils import get_step_text, replace_timers_in_step
from kptncook.models import Recipe
from kptncook.repositories import RecipeRepository
from kptncook.services import repository as service
from kptncook.services import workflows


def ingredient(quantity):
    return {
        "quantity": quantity,
        "ingredient": {
            "typ": "regular",
            "category": "test",
            "title": "Synthetic flour",
        },
    }


@pytest.mark.parametrize("quantity", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("where", ["ingredients", "steps"])
@pytest.mark.parametrize("json_input", [False, True])
def test_nonfinite_quantities_are_rejected(minimal, quantity, where, json_input):
    if where == "ingredients":
        minimal["ingredients"] = [ingredient(quantity)]
    else:
        minimal["steps"][0]["ingredients"] = [{"quantity": quantity}]
    validate = Recipe.model_validate_json if json_input else Recipe.model_validate
    with pytest.raises(ValidationError, match="finite"):
        validate(json.dumps(minimal) if json_input else minimal)


@pytest.mark.parametrize(
    "timer",
    [
        {"minOrExact": -1},
        {"max": -1},
        {"minOrExact": True},
        {"max": False},
        {"minOrExact": 5, "max": 4},
        {"minOrExact": "5"},
        {"max": 1.5},
    ],
)
def test_invalid_timer_payloads_refused(minimal, timer):
    minimal["steps"][0]["timers"] = [timer]
    with pytest.raises(ValidationError):
        Recipe.model_validate(minimal)


@pytest.mark.parametrize("count", [0, -1, True, False, "3", 1.5])
def test_invalid_explicit_servings_refused(minimal, count):
    minimal["fixedPortionCount"] = count
    with pytest.raises(ValidationError):
        Recipe.model_validate(minimal)


@pytest.mark.parametrize(
    "timer, compact, human",
    [
        ({"minOrExact": 0}, "0m", "0 Min."),
        ({"max": 4}, "≤4m", "bis zu 4 Min."),
        ({"minOrExact": 2, "max": 4}, "2–4m", "2–4 Min."),
    ],
)
def test_valid_timers_survive_export_without_mutation(minimal, timer, compact, human):
    minimal["steps"][0].update(title={"de": "Wait <timer>"}, timers=[timer])
    step = Recipe.model_validate(minimal).steps[0]
    before = copy.deepcopy(step)
    assert replace_timers_in_step(step, "Wait <timer>") == f"Wait {compact}"
    assert get_step_text(step) == f"Wait {human}"
    assert step == before


@pytest.mark.parametrize("quantity", [0, 0.25, None])
@pytest.mark.parametrize("portions", [None, 1, 7])
def test_valid_numeric_models_roundtrip(minimal, quantity, portions):
    minimal["ingredients"] = [ingredient(quantity)]
    minimal["steps"][0]["ingredients"] = [{"quantity": quantity}]
    minimal["fixedPortionCount"] = portions
    recipe = Recipe.model_validate(minimal)
    assert recipe.servings == (portions or 2)
    assert Recipe.model_validate_json(recipe.model_dump_json(by_alias=True)) == recipe


@pytest.mark.parametrize("invalid", ["quantity", "timer", "servings"])
@pytest.mark.parametrize("format", ["paprika", "tandoor", "markdown", "mealie"])
def test_invalid_stored_numeric_recipe_never_reaches_export(
    minimal, tmp_path, monkeypatch, invalid, format
):
    minimal["privatePayload"] = "synthetic-private-value"
    if invalid == "quantity":
        minimal["ingredients"] = [ingredient(float("nan"))]
    elif invalid == "timer":
        minimal["steps"][0]["timers"] = [{"minOrExact": -5}]
    else:
        minimal["fixedPortionCount"] = 0
    repo = RecipeRepository(tmp_path)
    repo.path.write_text(
        json.dumps([{"date": "2026-01-01", "data": minimal}]), encoding="utf-8"
    )
    monkeypatch.setattr(service, "get_repository", lambda: repo)
    if format == "mealie":
        client = Mock()
        monkeypatch.setattr(workflows, "get_mealie_client", lambda: client)
        monkeypatch.setattr(
            workflows, "get_kptncook_recipes_from_mealie", lambda client: []
        )
        result = workflows.sync_with_mealie_result()
        client.create_recipe.assert_not_called()
        client.close.assert_called_once()
    else:
        exporter = getattr(workflows, f"{format.capitalize()}Exporter")
        calls = []

        def export(*args, recipes):
            calls.extend(recipes)
            return [] if format != "paprika" else "empty"

        monkeypatch.setattr(exporter, "export", export)
        result = getattr(workflows, f"export_recipes_to_{format}_result")(None)
        assert calls == []
    assert len(result.invalid_repository_entries) == 1
    assert "synthetic-private-value" not in result.invalid_repository_entries[0].reason
    assert service.load_repository_recipes().recipes == []
