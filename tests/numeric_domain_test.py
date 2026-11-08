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


@pytest.mark.parametrize(
    "invalid", ["quantity", "timer", "servings", "preparation", "cooking"]
)
@pytest.mark.parametrize("format", ["paprika", "tandoor", "markdown", "mealie"])
def test_invalid_stored_numeric_recipe_never_reaches_export(
    minimal, tmp_path, monkeypatch, invalid, format
):
    minimal["privatePayload"] = "synthetic-private-value"
    if invalid == "quantity":
        minimal["ingredients"] = [ingredient(float("nan"))]
    elif invalid == "timer":
        minimal["steps"][0]["timers"] = [{"minOrExact": -5}]
    elif invalid == "servings":
        minimal["fixedPortionCount"] = 0
    elif invalid == "preparation":
        minimal["preparationTime"] = -2
    else:
        minimal["cookingTime"] = True
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


@pytest.mark.parametrize("field", ["preparationTime", "cookingTime"])
@pytest.mark.parametrize("value", [-2, True, False, 1.5, 1.0, "5", 525601, 10**30])
@pytest.mark.parametrize("json_input", [False, True])
def test_whole_recipe_duration_domain(minimal, field, value, json_input):
    minimal[field] = value
    validate = Recipe.model_validate_json if json_input else Recipe.model_validate
    with pytest.raises(ValidationError):
        validate(json.dumps(minimal) if json_input else minimal)


def test_preparation_duration_is_required_and_not_null(minimal):
    minimal.pop("preparationTime")
    with pytest.raises(ValidationError):
        Recipe.model_validate(minimal)
    minimal["preparationTime"] = None
    with pytest.raises(ValidationError):
        Recipe.model_validate(minimal)


@pytest.mark.parametrize("prep,cook", [(0, 0), (5, None), (525600, 525600)])
def test_valid_whole_recipe_durations_across_adapters(minimal, prep, cook):
    from kptncook.markdown_exporter import MarkdownExporter
    from kptncook.mealie import kptncook_to_mealie
    from kptncook.paprika import PaprikaExporter
    from kptncook.tandoor import TandoorExporter
    import yaml

    minimal.update(preparationTime=prep, cookingTime=cook, imageList=[], steps=[])
    recipe = Recipe.model_validate_json(json.dumps(minimal))
    assert Recipe.model_validate_json(recipe.model_dump_json(by_alias=True)) == recipe
    tandoor = TandoorExporter().get_recipe_payload(recipe)
    assert tandoor["working_time"] == prep
    assert tandoor["waiting_time"] == cook
    mealie = kptncook_to_mealie(recipe, api_key="synthetic")
    assert mealie.prep_time == prep
    assert mealie.cook_time == cook
    paprika = json.loads(PaprikaExporter().get_recipe_as_json_string(recipe))
    assert paprika["prep_time"] == str(prep)
    assert paprika["cook_time"] == ("" if cook is None else str(cook))
    markdown = yaml.safe_load(
        MarkdownExporter().render_recipe(recipe).split("---", 2)[1]
    )
    assert markdown["prepTime"] == f"{prep}m"
    if cook is None:
        assert "cookTime" not in markdown
    else:
        assert markdown["cookTime"] == f"{cook}m"


def test_scale_quantity_drops_float_noise_and_keeps_missing():
    from kptncook.models import scale_quantity

    assert scale_quantity(0.1, 3) == 0.3
    assert scale_quantity(None, 4) is None
    assert scale_quantity(0, 4) == 0
