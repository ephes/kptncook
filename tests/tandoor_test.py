import json
import zipfile

import httpx

from kptncook.models import Recipe
from kptncook.tandoor import IMAGE_DOWNLOAD_TIMEOUT, TandoorExporter


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
    assert {"name": "kptncook"} in payload["keywords"]
    assert {"name": "main_ingredient_pasta"} in payload["keywords"]
    assert {"name": "Fish"} in payload["keywords"]
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

    assert exporter.get_keywords(recipe) == [
        {"name": "kptncook"},
        {"name": "quick"},
        {"name": "dinner"},
        {"name": "Fish"},
    ]
    assert exporter.get_keywords(recipe).count({"name": "kptncook"}) == 1


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

    with zipfile.ZipFile(tmp_path / filename) as zip_file:
        payload = json.loads(zip_file.read("recipe.json").decode("utf-8"))
    instruction = payload["steps"][0]["instruction"]
    assert "15 Min." in instruction
    assert "<timer>" not in instruction


def _synthetic_recipe(minimal, oid: str, title: str) -> Recipe:
    data = json.loads(json.dumps(minimal))
    data["_id"] = {"$oid": oid}
    data["localizedTitle"] = {"de": title}
    recipe = Recipe.model_validate(data)
    recipe.image_list = []
    return recipe


def test_export_keeps_same_titled_recipes_apart(minimal, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = _synthetic_recipe(minimal, "aaaaaaaaaaaaaaaaaaaaaaaa", "Synthetic Soup")
    second = _synthetic_recipe(minimal, "bbbbbbbbbbbbbbbbbbbbbbbb", "Synthetic Soup")

    filenames = TandoorExporter().export([first, second])

    assert filenames == [
        "Synthetic_Soup.zip",
        "Synthetic_Soup-bbbbbbbbbbbbbbbbbbbbbbbb.zip",
    ]
    sources = []
    for filename in filenames:
        with zipfile.ZipFile(tmp_path / filename) as zip_file:
            payload = json.loads(zip_file.read("recipe.json").decode("utf-8"))
        sources.append(payload["source_url"])
    assert len(set(sources)) == 2
    zips = sorted(p.name for p in tmp_path.glob("*.zip"))
    assert zips == sorted(filenames)


def test_export_does_not_clobber_existing_file(minimal, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / "Synthetic_Soup.zip"
    existing.write_bytes(b"keep me")
    recipe = _synthetic_recipe(minimal, "cccccccccccccccccccccccc", "Synthetic Soup")

    first = TandoorExporter().export_recipe(recipe=recipe)
    second = TandoorExporter().export_recipe(recipe=recipe)

    assert existing.read_bytes() == b"keep me"
    assert first == "Synthetic_Soup-cccccccccccccccccccccccc.zip"
    assert second == "Synthetic_Soup-cccccccccccccccccccccccc-2.zip"
    assert zipfile.is_zipfile(tmp_path / first)
    assert zipfile.is_zipfile(tmp_path / second)
