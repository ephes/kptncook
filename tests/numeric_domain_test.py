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
    "invalid",
    [
        "quantity",
        "scaled",
        "scaled_step",
        "timer",
        "servings",
        "preparation",
        "cooking",
    ],
)
@pytest.mark.parametrize("format", ["paprika", "tandoor", "markdown", "mealie"])
def test_invalid_stored_numeric_recipe_never_reaches_export(
    minimal, tmp_path, monkeypatch, invalid, format
):
    minimal["privatePayload"] = "synthetic-private-value"
    if invalid == "quantity":
        minimal["ingredients"] = [ingredient(float("nan"))]
    elif invalid == "scaled":
        minimal["ingredients"] = [ingredient(1e308)]
    elif invalid == "scaled_step":
        minimal["steps"][0]["ingredients"] = [{"quantity": 1e308}]
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


@pytest.mark.parametrize("quantity", [1e308, -1e308])
@pytest.mark.parametrize("where", ["ingredients", "steps"])
@pytest.mark.parametrize("portions", [None, 3, 10**400])
def test_scaled_quantity_overflow_is_refused_at_recipe_admission(
    minimal, quantity, where, portions
):
    minimal["fixedPortionCount"] = portions
    if where == "ingredients":
        minimal["ingredients"] = [ingredient(quantity)]
    else:
        minimal["steps"][0]["ingredients"] = [{"quantity": quantity}]
    with pytest.raises(ValidationError, match=r"quantity.*finite"):
        Recipe.model_validate(minimal)


@pytest.mark.parametrize("format", ["paprika", "tandoor", "markdown", "mealie"])
def test_overflow_is_refused_before_export_effects(
    minimal, tmp_path, monkeypatch, format
):
    from kptncook.config import get_settings
    from kptncook.markdown_exporter import MarkdownExporter
    from kptncook.mealie import kptncook_to_mealie
    from kptncook.paprika import PaprikaExporter
    from kptncook.tandoor import TandoorExporter

    minimal["ingredients"] = [ingredient(1)]
    recipe = Recipe.model_validate(minimal)
    # Models are mutable: library adapters must preflight them too.
    recipe.ingredients[0].quantity = 1e308
    monkeypatch.chdir(tmp_path)
    sentinel = tmp_path / "previous-export.zip"
    sentinel.write_bytes(b"previous export")
    root = get_settings().root
    before = set(tmp_path.iterdir())
    image = Mock(side_effect=AssertionError("Image I/O before numeric admission"))
    for module in ("paprika", "tandoor", "markdown_exporter"):
        monkeypatch.setattr(f"kptncook.{module}.download_image", image)
    exports = {
        "paprika": lambda: PaprikaExporter().export([recipe]),
        "tandoor": lambda: TandoorExporter().export([recipe]),
        "markdown": lambda: MarkdownExporter().export([recipe]),
        "mealie": lambda: kptncook_to_mealie(recipe, api_key="synthetic"),
    }
    with pytest.raises(ValueError, match=r"ingredients.0.quantity.*finite"):
        exports[format]()
    image.assert_not_called()
    assert sentinel.read_bytes() == b"previous export"
    assert set(tmp_path.iterdir()) == before
    assert not (root / "export_md").exists()


def test_markdown_override_preflights_complete_batch(minimal, monkeypatch):
    from kptncook.config import get_settings
    from kptncook.markdown_exporter import MarkdownExporter

    minimal["ingredients"] = [ingredient(6e307)]  # Valid for the default 2 servings.
    recipe = Recipe.model_validate(minimal)
    ordinary = Recipe.model_validate(dict(minimal, ingredients=[]))
    image = Mock(side_effect=AssertionError("Image I/O before numeric admission"))
    monkeypatch.setattr("kptncook.markdown_exporter.download_image", image)
    with pytest.raises(ValueError, match="quantity.*finite"):
        MarkdownExporter(servings=4).export([ordinary, recipe])
    image.assert_not_called()
    assert not (get_settings().root / "export_md").exists()


@pytest.mark.parametrize("quantity", [None, 0, 0.1, 1e307])
def test_finite_scaled_quantities_in_actual_export_payloads(
    minimal, tmp_path, monkeypatch, quantity
):
    import gzip
    import zipfile

    import yaml

    from kptncook.markdown_exporter import MarkdownExporter
    from kptncook.mealie import kptncook_to_mealie
    from kptncook.paprika import PaprikaExporter
    from kptncook.tandoor import TandoorExporter

    minimal.update(
        ingredients=[ingredient(quantity)], fixedPortionCount=3, imageList=[]
    )
    minimal["steps"][0].update(ingredients=[ingredient(quantity)])
    recipe = Recipe.model_validate(minimal)
    monkeypatch.chdir(tmp_path)
    for module in ("paprika", "tandoor", "markdown_exporter"):
        monkeypatch.setattr(f"kptncook.{module}.download_image", lambda *a: None)

    def strict_json(raw):
        def refuse(value):
            pytest.fail(f"Nonfinite JSON constant: {value}")

        return json.loads(raw, parse_constant=refuse)

    expected = None if quantity is None else (0.3 if quantity == 0.1 else quantity * 3)
    [tandoor_path] = TandoorExporter().export([recipe])
    with zipfile.ZipFile(tandoor_path) as archive:
        tandoor = strict_json(archive.read("recipe.json"))
    assert tandoor["ingredients"][0].get("amount") == expected
    assert tandoor["steps"][0]["ingredients"][0].get("amount") == expected
    with zipfile.ZipFile(PaprikaExporter().export([recipe])) as archive:
        paprika = strict_json(gzip.decompress(archive.read(archive.namelist()[0])))
    [note] = MarkdownExporter().export([recipe])
    text = note.read_text(encoding="utf-8")
    assert yaml.safe_load(text.split("---", 2)[1])["servings"] == 3
    assert "inf" not in paprika["ingredients"].lower()
    assert "inf" not in text.lower()
    if expected:
        assert f"{expected:g}" in paprika["ingredients"]
        assert f"{expected:g}" in text
    mealie = strict_json(
        kptncook_to_mealie(recipe, api_key="synthetic").model_dump_json()
    )
    assert mealie["recipe_ingredient"][0]["quantity"] == expected


def test_zero_and_missing_scale_with_very_large_valid_servings():
    from kptncook.models import scale_quantity

    assert scale_quantity(0, 10**400) == 0
    assert scale_quantity(None, 10**400) is None


def test_markdown_override_cli_reports_field_without_output(
    minimal, monkeypatch, tmp_path
):
    from typer.testing import CliRunner
    from kptncook import cli
    from kptncook.config import get_settings

    minimal["ingredients"] = [ingredient(6e307)]
    repo = RecipeRepository(tmp_path)
    repo.path.write_text(json.dumps([{"date": "2026-01-01", "data": minimal}]))
    monkeypatch.setattr(service, "get_repository", lambda: repo)
    result = CliRunner().invoke(cli, ["export-recipes-to-markdown", "--servings", "4"])
    assert result.exit_code == 1
    assert "ingredients.0.quantity" in result.output
    assert "finite" in result.output
    assert not (get_settings().root / "export_md").exists()
