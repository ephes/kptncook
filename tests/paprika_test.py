import json
import os

import httpx
import pytest

from kptncook.models import Recipe
from kptncook.paprika import GeneratedData, PaprikaExporter


def test_asciify_string():
    p = PaprikaExporter()
    assert (
        p.asciify_string("Süßkartoffeln mit Taboulé & Dip")
        == "Susskartoffeln_mit_Taboule___Dip"
    )
    assert p.asciify_string("Ölige_Ähren") == "Olige_Ahren"


def test_get_cover_img_as_base64_string(full_recipe, mocker):
    p = PaprikaExporter()
    recipe = Recipe.model_validate(full_recipe)
    mocker.patch(
        "kptncook.paprika.httpx.get",
        return_value=mocker.Mock(content=b"foobar", status_code=200),
    )
    cover_info = p.get_cover_img_as_base64_string(recipe=recipe)
    assert isinstance(cover_info, tuple) is True
    assert len(cover_info) == 2

    # no images available for some reason
    recipe.image_list = list()
    with pytest.raises(ValueError):
        p.get_cover_img_as_base64_string(recipe=recipe)


def test_get_cover_img_as_base64_string_can_handle_404(full_recipe, mocker):
    p = PaprikaExporter()
    recipe = Recipe.model_validate(full_recipe)
    mocker.patch(
        "kptncook.paprika.httpx.get",
        return_value=mocker.Mock(content=b"foobar", status_code=404),
    )
    # hm, looks weird, but works.
    m = mocker.patch("kptncook.paprika.httpx.get")
    mock_response = mocker.Mock()
    mock_response.raise_for_status = mocker.Mock(
        side_effect=httpx.HTTPStatusError(
            message="404 File not found", response=mock_response, request=mocker.Mock()
        )
    )
    m.return_value = mock_response
    assert p.get_cover_img_as_base64_string(recipe=recipe) == (None, None)


def test_export_single_recipe(full_recipe, mocker):
    p = PaprikaExporter()
    recipe = Recipe.model_validate(full_recipe)
    mocker.patch(
        "kptncook.paprika.httpx.get",
        return_value=mocker.Mock(content=b"foobar", status_code=200),
    )
    p.export(recipes=[recipe])
    expected_file = (
        "Uberbackene_Muschelnudeln_mit_Lachs___Senf_Dill_Sauce.paprikarecipes"
    )
    assert os.path.isfile(expected_file) is True
    if os.path.isfile(expected_file):
        os.unlink(expected_file)


def test_export_all_recipes(full_recipe, minimal, mocker):
    p = PaprikaExporter()
    recipe1 = Recipe.model_validate(full_recipe)
    recipe2 = Recipe.model_validate(minimal)
    mocker.patch(
        "kptncook.paprika.httpx.get",
        return_value=mocker.Mock(content=b"foobar", status_code=200),
    )
    p.export(recipes=[recipe1, recipe2])
    expected_file = "allrecipes.paprikarecipes"
    assert os.path.isfile(expected_file) is True
    if os.path.isfile(expected_file):
        os.unlink(expected_file)


def test_get_cover(minimal):
    p = PaprikaExporter()
    recipe = Recipe.model_validate(minimal)
    assert p.get_cover(image_list=list()) is None

    cover = p.get_cover(image_list=recipe.image_list)
    assert cover.name == "REZ_1837_Cover.jpg"

    with pytest.raises(ValueError):
        p.get_cover(image_list=None)

    with pytest.raises(ValueError):
        p.get_cover(image_list=dict())


def test_render(minimal, mocker):
    # happy path
    recipe = Recipe.model_validate(minimal)
    paprika_exporter = PaprikaExporter()
    generated = GeneratedData("", "", "", "")
    mocker.patch.object(
        paprika_exporter, "get_generated_data", return_value=generated, autospec=True
    )
    export_data = paprika_exporter.get_export_data(recipes=[recipe])
    [actual] = list(export_data.values())
    print("actual: ", actual)
    expected = (
        "{\n"
        '   "uid":"5e5390e2740000cdf1381c64",\n'
        '   "name":"Minimal Recipe",\n'
        '   "directions": "Alles parat?\\n",\n'
        '   "servings":"2",\n'
        '   "rating":0,\n'
        '   "difficulty":"",\n'
        '   "ingredients":"",\n'
        '   "notes":"",\n'
        '   "created":"",\n'
        '   "image_url":null,\n'
        '   "cook_time":"",\n'
        '   "prep_time":"20",\n'
        '   "source":"Kptncook",\n'
        '   "source_url":"",\n'
        '   "hash" : "",\n'
        '   "photo_hash":null,\n'
        '   "photos":[],\n'
        '   "photo": "",\n'
        '   "nutritional_info":"calories: 100\\nprotein: 30\\nfat: 10\\ncarbohydrate: '
        '20\\n",\n'
        '   "photo_data":"",\n'
        '   "photo_large":null,\n'
        '   "categories":["Kptncook"]\n'
        "}"
    )
    expected = PaprikaExporter.unescaped_newline.sub(" ", expected)
    assert actual == expected


def test_filter_unescaped_newline(minimal):
    """
    Test that unescaped newlines are converted to a space.

    Unescaped newlines in the recipe title are invalid in JSON, so
    Paprika would not be able to import the recipe.
    """
    # Given a recipe with an unescaped newline in the title
    recipe = Recipe.model_validate(minimal)
    recipe.steps[0].title.de = "Alles parat?\n"  # add unescaped newline
    paprika_exporter = PaprikaExporter()
    # When the recipe is rendered as a JSON string
    json_string = paprika_exporter.get_recipe_as_json_string(recipe=recipe)
    data = json.loads(json_string)
    # Then the unescaped newline is converted to a space
    assert data["directions"] == "Alles parat? \n"
