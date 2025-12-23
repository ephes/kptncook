import json
import zipfile

import httpx

from kptncook.models import Recipe
from kptncook.tandoor import TandoorExporter


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
    with zipfile.ZipFile(zip_path) as zip_file:
        assert set(zip_file.namelist()) == {"recipe.json", "image.jpg"}
        payload = json.loads(zip_file.read("recipe.json").decode("utf-8"))
    assert payload["name"] == recipe.localized_title.de
    assert "kptncook" in payload["keywords"]
    assert "main_ingredient_pasta" in payload["keywords"]
    assert "Fish" in payload["keywords"]
    httpx_get.assert_called_once_with(
        "https://example.com/cover.jpg", follow_redirects=True
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
    with zipfile.ZipFile(zip_path) as zip_file:
        assert "recipe.json" in zip_file.namelist()
        assert "image.jpg" not in zip_file.namelist()


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
    with zipfile.ZipFile(zip_path) as zip_file:
        assert "recipe.json" in zip_file.namelist()
        assert "image.jpg" not in zip_file.namelist()
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
