import json
import zipfile

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


def test_export_single_recipe(full_recipe, mocker, tmp_path, monkeypatch):
    p = PaprikaExporter()
    recipe = Recipe.model_validate(full_recipe)
    monkeypatch.chdir(tmp_path)
    mocker.patch(
        "kptncook.paprika.httpx.get",
        return_value=mocker.Mock(content=b"foobar", status_code=200),
    )
    p.export(recipes=[recipe])
    expected_file = (
        "Uberbackene_Muschelnudeln_mit_Lachs___Senf_Dill_Sauce.paprikarecipes"
    )
    expected_path = tmp_path / expected_file
    assert expected_path.is_file() is True


def test_export_all_recipes(full_recipe, minimal, mocker, tmp_path, monkeypatch):
    p = PaprikaExporter()
    recipe1 = Recipe.model_validate(full_recipe)
    recipe2 = Recipe.model_validate(minimal)
    monkeypatch.chdir(tmp_path)
    mocker.patch(
        "kptncook.paprika.httpx.get",
        return_value=mocker.Mock(content=b"foobar", status_code=200),
    )
    p.export(recipes=[recipe1, recipe2])
    expected_file = "allrecipes.paprikarecipes"
    expected_path = tmp_path / expected_file
    assert expected_path.is_file() is True


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
    assert json.loads(actual) == json.loads(expected)


def test_render_expands_timer_placeholders(minimal, mocker):
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
    paprika_exporter = PaprikaExporter()
    generated = GeneratedData("", "", "", "")
    mocker.patch.object(
        paprika_exporter, "get_generated_data", return_value=generated, autospec=True
    )
    export_data = paprika_exporter.get_export_data(recipes=[recipe])
    [actual] = list(export_data.values())
    data = json.loads(actual)
    assert "15 Min." in data["directions"]
    assert "<timer>" not in data["directions"]


@pytest.mark.usefixtures("offline_generated_data")
def test_preserve_newline(minimal):
    recipe = Recipe.model_validate(minimal)
    recipe.steps[0].title.de = "Alles parat?\n"
    data = json.loads(PaprikaExporter().get_recipe_as_json_string(recipe))
    assert data["directions"] == "Alles parat?\n\n"


def _synthetic_recipe(minimal, oid: str, title: str) -> Recipe:
    data = dict(minimal)
    data["_id"] = {"$oid": oid}
    data["localizedTitle"] = {"de": title}
    return Recipe.model_validate(data)


@pytest.fixture
def offline_generated_data(mocker):
    return mocker.patch.object(
        PaprikaExporter,
        "get_generated_data",
        return_value=GeneratedData(None, None, "2026-01-01 00:00:00", "0" * 64),
    )


@pytest.mark.usefixtures("offline_generated_data")
def test_export_same_titled_recipes_produce_distinct_files(
    minimal, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    p = PaprikaExporter()
    first = _synthetic_recipe(minimal, "aaaaaaaaaaaaaaaaaaaaaaaa", "Synthetic Stew")
    second = _synthetic_recipe(minimal, "bbbbbbbbbbbbbbbbbbbbbbbb", "Synthetic Stew")

    first_name = p.export(recipes=[first])
    second_name = p.export(recipes=[second])

    assert first_name == "Synthetic_Stew.paprikarecipes"
    assert second_name == "Synthetic_Stew-bbbbbbbbbbbbbbbbbbbbbbbb.paprikarecipes"
    assert (tmp_path / first_name).is_file()
    assert (tmp_path / second_name).is_file()


@pytest.mark.usefixtures("offline_generated_data")
def test_export_does_not_clobber_existing_file(minimal, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Synthetic_Stew.paprikarecipes").write_bytes(b"keep single")
    (tmp_path / "allrecipes.paprikarecipes").write_bytes(b"keep all")
    p = PaprikaExporter()
    first = _synthetic_recipe(minimal, "aaaaaaaaaaaaaaaaaaaaaaaa", "Synthetic Stew")
    second = _synthetic_recipe(minimal, "bbbbbbbbbbbbbbbbbbbbbbbb", "Other Stew")

    single = p.export(recipes=[first])
    combined = p.export(recipes=[first, second])

    assert single == "Synthetic_Stew-aaaaaaaaaaaaaaaaaaaaaaaa.paprikarecipes"
    assert combined == "allrecipes-2.paprikarecipes"
    assert (tmp_path / "Synthetic_Stew.paprikarecipes").read_bytes() == b"keep single"
    assert (tmp_path / "allrecipes.paprikarecipes").read_bytes() == b"keep all"
    assert zipfile.is_zipfile(tmp_path / single)
    assert zipfile.is_zipfile(tmp_path / combined)


@pytest.mark.usefixtures("offline_generated_data")
def test_export_text_roundtrip(full_recipe, minimal, tmp_path, monkeypatch):
    import gzip

    monkeypatch.chdir(tmp_path)
    text = 'Quoted "food" \\folder\t日本語\nnext'
    recipe = Recipe.model_validate(full_recipe)
    recipe.localized_title.de = text
    recipe.steps[0].title.de = text
    recipe.ingredients[0].ingredient.uncountable_title.de = text
    exporter = PaprikaExporter()
    filename = exporter.export([recipe, Recipe.model_validate(minimal)])
    with zipfile.ZipFile(tmp_path / filename) as archive:
        assert len(archive.namelist()) == 2
        data = json.loads(
            gzip.decompress(archive.read(f"recipe_{recipe.id.oid}.paprikarecipe"))
        )
    assert data["name"] == text
    assert data["directions"].startswith(text + "\n")
    assert text in data["ingredients"]


@pytest.mark.usefixtures("offline_generated_data")
@pytest.mark.parametrize("failure", [None, "zip", "move"])
def test_export_cleans_workdir(minimal, tmp_path, monkeypatch, mocker, failure):
    workdir = tmp_path / "temporary"
    workdir.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(workdir))
    monkeypatch.chdir(tmp_path)
    if failure:
        name = "write_zip" if failure == "zip" else "move_to_free_target"
        mocker.patch(f"kptncook.paprika.{name}", side_effect=OSError("injected"))
        with pytest.raises(OSError, match="injected"):
            PaprikaExporter().export([Recipe.model_validate(minimal)])
    else:
        filename = PaprikaExporter().export([Recipe.model_validate(minimal)])
        assert (tmp_path / filename).is_file()
    assert list(workdir.iterdir()) == []


@pytest.mark.usefixtures("offline_generated_data")
def test_missing_cover_is_empty(minimal):
    data = json.loads(
        PaprikaExporter().get_recipe_as_json_string(Recipe.model_validate(minimal))
    )
    assert data["photo"] == ""
    assert data["photo_data"] == ""
