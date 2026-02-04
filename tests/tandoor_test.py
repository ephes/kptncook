import json
import zipfile
from io import BytesIO

import httpx

from kptncook.models import Recipe
from kptncook.tandoor import (
    IMAGE_DOWNLOAD_TIMEOUT,
    TANDOOR_BULK_EXPORT_FILENAME,
    TandoorExporter,
)


def _read_recipe_from_export_zip(zip_path, inner_name="recipe.zip"):
    """Open outer zip, read inner recipe zip, return (payload, inner_namelist)."""
    with zipfile.ZipFile(zip_path) as outer:
        inner_bytes = outer.read(inner_name)
    with zipfile.ZipFile(BytesIO(inner_bytes)) as inner:
        payload = json.loads(inner.read("recipe.json").decode("utf-8"))
        return payload, inner.namelist()


def test_export_produces_single_bulk_zip(
    full_recipe, minimal, mocker, tmp_path, monkeypatch
):
    exporter = TandoorExporter()
    recipe1 = Recipe.model_validate(full_recipe)
    recipe2 = Recipe.model_validate(minimal)
    monkeypatch.chdir(tmp_path)
    mocker.patch.object(
        Recipe,
        "get_image_url",
        autospec=True,
        return_value="https://example.com/cover.jpg",
    )
    mocker.patch(
        "kptncook.tandoor.httpx.get",
        return_value=mocker.Mock(content=b"image", raise_for_status=mocker.Mock()),
    )

    filenames = exporter.export(recipes=[recipe1, recipe2])

    assert filenames == [TANDOOR_BULK_EXPORT_FILENAME]
    zip_path = tmp_path / TANDOOR_BULK_EXPORT_FILENAME
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as bulk:
        names = sorted(bulk.namelist())
        assert len(names) == 2
        assert all(n.endswith(".zip") for n in names)
    payload, inner_names = _read_recipe_from_export_zip(zip_path, inner_name=names[0])
    assert "name" in payload
    assert set(inner_names) >= {"recipe.json"}


def test_export_returns_empty_list_when_no_recipes():
    exporter = TandoorExporter()
    assert exporter.export(recipes=[]) == []


def test_export_recipe_writes_zip_with_image(
    full_recipe, mocker, tmp_path, monkeypatch
):
    exporter = TandoorExporter()
    recipe = Recipe.model_validate(full_recipe)
    monkeypatch.chdir(tmp_path)
    mocker.patch.object(
        Recipe,
        "get_image_url",
        autospec=True,
        return_value="https://example.com/cover.jpg",
    )
    mock_response = mocker.Mock()
    mock_response.content = b"image-bytes"
    mock_response.raise_for_status = mocker.Mock()
    httpx_get = mocker.patch("kptncook.tandoor.httpx.get", return_value=mock_response)

    filename = exporter.export_recipe(recipe=recipe)

    zip_path = tmp_path / filename
    assert zip_path.is_file()
    payload, inner_names = _read_recipe_from_export_zip(zip_path)
    assert set(inner_names) == {"recipe.json", "image.jpg"}
    assert payload["name"] == recipe.localized_title.de
    keyword_names = [k["name"] for k in payload["keywords"]]
    assert "kptncook" in keyword_names
    assert "main_ingredient_pasta" in keyword_names
    assert "Fish" in keyword_names
    assert "working_time" in payload
    assert "waiting_time" in payload
    assert "prep_time" not in payload
    assert "cook_time" not in payload
    httpx_get.assert_called_once_with(
        "https://example.com/cover.jpg",
        follow_redirects=True,
        timeout=IMAGE_DOWNLOAD_TIMEOUT,
    )


def test_export_recipe_skips_missing_cover_image(
    full_recipe, mocker, tmp_path, monkeypatch
):
    exporter = TandoorExporter()
    recipe = Recipe.model_validate(full_recipe)
    monkeypatch.chdir(tmp_path)
    mocker.patch.object(
        Recipe,
        "get_image_url",
        autospec=True,
        return_value="https://example.com/missing.jpg",
    )
    mock_response = mocker.Mock()
    mock_response.status_code = 404
    mock_response.raise_for_status = mocker.Mock(
        side_effect=httpx.HTTPStatusError(
            message="404 Not Found",
            request=mocker.Mock(),
            response=mock_response,
        )
    )
    mocker.patch("kptncook.tandoor.httpx.get", return_value=mock_response)

    filename = exporter.export_recipe(recipe=recipe)

    zip_path = tmp_path / filename
    assert zip_path.is_file()
    _, inner_names = _read_recipe_from_export_zip(zip_path)
    assert "recipe.json" in inner_names
    assert "image.jpg" not in inner_names


def test_export_recipe_skips_when_no_cover_image(
    full_recipe, mocker, tmp_path, monkeypatch
):
    exporter = TandoorExporter()
    recipe = Recipe.model_validate(full_recipe)
    recipe.image_list = []
    monkeypatch.chdir(tmp_path)
    httpx_get = mocker.patch("kptncook.tandoor.httpx.get")
    get_image_url = mocker.patch.object(Recipe, "get_image_url", autospec=True)

    filename = exporter.export_recipe(recipe=recipe)

    zip_path = tmp_path / filename
    assert zip_path.is_file()
    _, inner_names = _read_recipe_from_export_zip(zip_path)
    assert "recipe.json" in inner_names
    assert "image.jpg" not in inner_names
    httpx_get.assert_not_called()
    get_image_url.assert_not_called()


def test_get_source_url_prefers_uid(full_recipe, minimal):
    exporter = TandoorExporter()
    with_uid = Recipe.model_validate(full_recipe)
    assert exporter.get_source_url(with_uid) == (
        f"https://share.kptncook.com/{with_uid.uid}"
    )

    without_uid = Recipe.model_validate(minimal)
    assert without_uid.uid is None
    assert exporter.get_source_url(without_uid) == (
        f"https://share.kptncook.com/{without_uid.id.oid}"
    )


def test_get_keywords_includes_active_tags_and_rtype(minimal):
    exporter = TandoorExporter()
    recipe_data = {
        **minimal,
        "activeTags": ["kptncook", "quick", "dinner"],
        "rtype": "Fish",
    }
    recipe = Recipe.model_validate(recipe_data)

    assert exporter.get_keywords(recipe) == ["kptncook", "quick", "dinner", "Fish"]
    assert exporter.get_keywords(recipe).count("kptncook") == 1


def test_get_recipe_payload_uses_tandoor_time_fields(minimal):
    exporter = TandoorExporter()
    recipe = Recipe.model_validate({**minimal, "cookingTime": 45})

    payload = exporter.get_recipe_payload(recipe)

    assert payload["working_time"] == 20
    assert payload["waiting_time"] == 45
    assert "prep_time" not in payload
    assert "cook_time" not in payload


def test_get_step_ingredients_skips_unnamed_ingredients(minimal, caplog):
    exporter = TandoorExporter()
    recipe_data = {
        **minimal,
        "steps": [
            {
                "title": {"de": "Alles parat?"},
                "ingredients": [{"quantity": 1, "unit": "g"}],
                "image": minimal["steps"][0]["image"],
            }
        ],
    }
    recipe = Recipe.model_validate(recipe_data)

    with caplog.at_level("DEBUG", logger="kptncook.tandoor"):
        ingredients = exporter.get_step_ingredients(recipe.steps[0])

    assert ingredients == []
    assert "without a resolvable food name" in caplog.text


def test_get_recipe_payload_uses_empty_description_without_author_comment(minimal):
    exporter = TandoorExporter()
    recipe_data = {**minimal}
    recipe_data.pop("authorComment")
    recipe = Recipe.model_validate(recipe_data)

    payload = exporter.get_recipe_payload(recipe)

    assert payload["description"] == ""


def test_export_expands_timer_placeholders(minimal, mocker, tmp_path, monkeypatch):
    exporter = TandoorExporter()
    recipe_data = {
        **minimal,
        "steps": [
            {
                "title": {"de": "Kartoffeln ca. <timer> kochen."},
                "ingredients": [],
                "image": minimal["steps"][0]["image"],
                "timers": [{"minOrExact": 15}],
            }
        ],
    }
    recipe = Recipe.model_validate(recipe_data)
    monkeypatch.chdir(tmp_path)
    mocker.patch.object(
        Recipe,
        "get_image_url",
        autospec=True,
        return_value="https://example.com/cover.jpg",
    )
    mocker.patch(
        "kptncook.tandoor.httpx.get",
        return_value=mocker.Mock(content=b"image", raise_for_status=mocker.Mock()),
    )

    filename = exporter.export_recipe(recipe=recipe)

    payload, _ = _read_recipe_from_export_zip(tmp_path / filename)
    instruction = payload["steps"][0]["instruction"]
    assert "15 Min." in instruction
    assert "<timer>" not in instruction


def test_export_skips_step_ingredients_with_empty_name(
    minimal, mocker, tmp_path, monkeypatch
):
    exporter = TandoorExporter()
    recipe_data = {
        **minimal,
        "steps": [
            {
                "title": {"de": "Step with unresolved ingredients"},
                "ingredients": [
                    {"ingredientId": "abc123"},
                    {"ingredientId": "def456", "ingredient": {}},
                ],
                "image": minimal["steps"][0]["image"],
            }
        ],
    }
    recipe = Recipe.model_validate(recipe_data)
    monkeypatch.chdir(tmp_path)
    mocker.patch.object(
        Recipe,
        "get_image_url",
        autospec=True,
        return_value="https://example.com/cover.jpg",
    )
    mocker.patch(
        "kptncook.tandoor.httpx.get",
        return_value=mocker.Mock(content=b"image", raise_for_status=mocker.Mock()),
    )

    filename = exporter.export_recipe(recipe=recipe)

    payload, _ = _read_recipe_from_export_zip(tmp_path / filename)
    step_ingredients = payload["steps"][0]["ingredients"]
    assert step_ingredients == []
