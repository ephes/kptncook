import json
import os

import httpx
import pytest

from kptncook.models import Recipe
from kptncook.tandoor import GeneratedTandoorData, TandoorExporter


def test_asciify_string():
    tandoor_exporter = TandoorExporter()
    assert (
        tandoor_exporter.asciify_string("Süßkartoffeln mit Taboulé & Dip")
        == "Susskartoffeln_mit_Taboule___Dip"
    )
    assert tandoor_exporter.asciify_string("Ölige_Ähren") == "Olige_Ahren"


def test_get_cover_img_as_bytes(full_recipe, mocker):
    tandoor_exporter = TandoorExporter()
    recipe = Recipe.model_validate(full_recipe)
    cover_info = tandoor_exporter.get_cover_img_as_bytes(recipe=recipe)
    assert isinstance(cover_info, tuple) is True
    assert len(cover_info) == 2

    # no images available for some reason
    recipe.image_list = list()
    with pytest.raises(ValueError):
        tandoor_exporter.get_cover_img_as_bytes(recipe=recipe)


def test_get_cover_img_as_bytes_can_handle_404(full_recipe, mocker):
    p = TandoorExporter()
    recipe = Recipe.model_validate(full_recipe)
    mocker.patch(
        "kptncook.tandoor.httpx.get",
        return_value=mocker.Mock(content=b"foobar", status_code=404),
    )
    # hm, looks weird, but works.
    m = mocker.patch("kptncook.tandoor.httpx.get")
    mock_response = mocker.Mock()
    mock_response.raise_for_status = mocker.Mock(
        side_effect=httpx.HTTPStatusError(
            message="404 File not found", response=mock_response, request=mocker.Mock()
        )
    )
    m.return_value = mock_response
    assert p.get_cover_img_as_bytes(recipe=recipe) == (None, None)


def test_export_single_recipe(full_recipe, mocker):
    tandoor_exporter = TandoorExporter()
    recipe = Recipe.model_validate(full_recipe)
    mocker.patch(
        "kptncook.tandoor.httpx.get",
        return_value=mocker.Mock(content=b"foobar", status_code=200),
    )
    tandoor_exporter.export(recipes=[recipe])
    expected_file = "Uberbackene_Muschelnudeln_mit_Lachs___Senf_Dill_Sauce.zip"
    assert os.path.isfile(expected_file) is True
    if os.path.isfile(expected_file):
        os.unlink(expected_file)


def test_export_all_recipes(full_recipe, minimal, mocker):
    tandoor_exporter = TandoorExporter()
    recipe1 = Recipe.model_validate(full_recipe)
    recipe2 = Recipe.model_validate(minimal)
    mocker.patch(
        "kptncook.tandoor.httpx.get",
        return_value=mocker.Mock(content=b"foobar", status_code=200),
    )
    tandoor_exporter.export(recipes=[recipe1, recipe2])
    expected_file = "allrecipes.zip"
    assert os.path.isfile(expected_file) is True
    if os.path.isfile(expected_file):
        os.unlink(expected_file)


def test_get_cover(minimal):
    tandoor_exporter = TandoorExporter()
    recipe = Recipe.model_validate(minimal)
    assert tandoor_exporter.get_cover(image_list=list()) is None

    cover = tandoor_exporter.get_cover(image_list=recipe.image_list)
    assert cover.name == "REZ_1837_Cover.jpg"

    with pytest.raises(ValueError):
        tandoor_exporter.get_cover(image_list=None)

    with pytest.raises(ValueError):
        tandoor_exporter.get_cover(image_list=dict())


def test_render(minimal, mocker):
    # happy path
    recipe = Recipe.model_validate(minimal)
    tandoor_exporter = TandoorExporter()
    generated = GeneratedTandoorData("", b"", "", "")
    mocker.patch.object(
        tandoor_exporter, "get_generated_data", return_value=generated, autospec=True
    )
    export_data = tandoor_exporter.get_export_data(recipes=[recipe])
    actual = json.loads(export_data["5e5390e2740000cdf1381c64"].json)
    print("actual: ", actual)
    expected = json.loads(
        "{"
        '   "name": "Minimal Recipe",'
        '   "description": "Dies ist ein Kommentar"'
        ',   "keywords": ['
        "    {"
        '       "name": "Kptncook",'
        '       "description": ""'
        "     }"
        "   ],"
        '   "working_time": 20,'
        '   "waiting_time": 0,'
        '   "servings": 3,'
        '   "servings_text": "Portionen",'
        '   "internal": true,'
        '   "source_url": "https://mobile.kptncook.com/recipe/pinterest/Minimal%20Recipe/1234",'
        '   "nutrition": null,'
        '   "steps": ['
        "     {"
        '       "name": "",'
        '       "instruction": "Alles parat?",'
        '       "time": 0,'
        '       "order": 0,'
        '       "show_ingredients_table": false,'
        '       "ingredients": []'
        "    }"
        "  ]"
        " }"
    )
    assert actual == expected


def test_step_without_ingredients_doesnt_show_ingredients_table(minimal):
    recipe = Recipe.model_validate(minimal)
    tandoor_exporter = TandoorExporter()

    # When the recipe is rendered as a JSON string
    generated = tandoor_exporter.get_generated_data(recipe=recipe)
    json_string = tandoor_exporter.get_recipe_as_json_string(
        recipe=recipe, generated=generated
    )
    data = json.loads(json_string)

    assert data["steps"][0]["show_ingredients_table"] is False


def test_step_with_ingredients_shows_ingredients_table(full_recipe):
    recipe = Recipe.model_validate(full_recipe)
    tandoor_exporter = TandoorExporter()

    # When the recipe is rendered as a JSON string
    generated = tandoor_exporter.get_generated_data(recipe=recipe)
    json_string = tandoor_exporter.get_recipe_as_json_string(
        recipe=recipe, generated=generated
    )
    data = json.loads(json_string)

    assert data["steps"][1]["show_ingredients_table"] is True


def test_step_with_ingredient_without_unit(full_recipe):
    recipe = Recipe.model_validate(full_recipe)
    tandoor_exporter = TandoorExporter()

    # When the recipe is rendered as a JSON string
    generated = tandoor_exporter.get_generated_data(recipe=recipe)
    json_string = tandoor_exporter.get_recipe_as_json_string(
        recipe=recipe, generated=generated
    )
    data = json.loads(json_string)

    assert data["steps"][3]["ingredients"][1]["food"]["name"] == "Salz"
    assert data["steps"][3]["ingredients"][1]["unit"] is None
    assert data["steps"][3]["ingredients"][1]["no_amount"] is True


@pytest.mark.skip(
    reason="missing feature, see https://github.com/TandoorRecipes/recipes/issues/3554"
)
def test_export_contains_step_images():
    pass


def test_filter_unescaped_newline(minimal):
    # Given a recipe with an unescaped newline in the title
    recipe = Recipe.model_validate(minimal)
    recipe.steps[0].title.de = "Alles parat?\n"  # add unescaped newline
    tandoor_exporter = TandoorExporter()
    # When the recipe is rendered as a JSON string
    generated = tandoor_exporter.get_generated_data(recipe=recipe)
    json_string = tandoor_exporter.get_recipe_as_json_string(
        recipe=recipe, generated=generated
    )
    data = json.loads(json_string)
    # Then the unescaped newline is converted to a space
    assert data["steps"][0]["instruction"] == "Alles parat?\n"
