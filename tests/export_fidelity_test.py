"""Synthetic, offline checks of local selection and serialized recipe metadata."""

import gzip
import json
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from kptncook.markdown_exporter import MarkdownExporter
from kptncook.mealie import kptncook_to_mealie
from kptncook.models import Image, Recipe
from kptncook.paprika import GeneratedData, PaprikaExporter
from kptncook.services import workflows
from kptncook.services.repository import RepositoryRecipesResult
from kptncook.tandoor import TandoorExporter


@pytest.fixture
def synthetic_recipe():
    return Recipe.model_validate(
        {
            "_id": {"$oid": "0123456789abcdef01234567"},
            "uid": "Abc1234",
            "localizedTitle": {"en": "Synthetic soup"},
            "preparationTime": 5,
            "recipeNutrition": {
                "calories": 100,
                "protein": 1,
                "fat": 2,
                "carbohydrate": 3,
            },
            "steps": [],
            "imageList": [],
            "ingredients": [
                {
                    "quantity": 40,
                    "measure": "g",
                    "ingredient": {
                        "typ": "regular",
                        "category": "baking",
                        "localizedTitle": {"en": "Flour"},
                        "uncountableTitle": {"en": "Flour"},
                    },
                }
            ],
        }
    )


@pytest.fixture
def offline_exports(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TandoorExporter, "get_cover_image_bytes", lambda *a, **kw: None)
    monkeypatch.setattr(
        PaprikaExporter,
        "get_generated_data",
        lambda *a, **kw: GeneratedData(None, None, "2026-11-07", "synthetic"),
    )
    monkeypatch.setattr(
        workflows,
        "KptnCookClient",
        lambda: pytest.fail("local export used network client"),
    )


def read_export(kind, result):
    if kind == "paprika":
        with ZipFile(result.filename) as archive:
            return json.loads(gzip.decompress(archive.read(archive.namelist()[0])))
    if kind == "tandoor":
        with ZipFile(result.filenames[0]) as archive:
            return json.loads(archive.read("recipe.json"))
    return yaml.safe_load(
        Path(result.filenames[0]).read_text(encoding="utf-8").split("---", 2)[1]
    )


@pytest.mark.parametrize("kind", ["paprika", "tandoor", "markdown"])
@pytest.mark.parametrize("selector", ["uid", "oid", "uid-url", "oid-url"])
def test_local_exports_resolve_both_identifiers(
    monkeypatch, offline_exports, synthetic_recipe, kind, selector
):
    recipe = synthetic_recipe
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult([recipe], []),
    )
    value = recipe.uid if selector.startswith("uid") else recipe.id.oid
    if selector.endswith("url"):
        value = f"https://share.kptncook.com/{value}?x=1#part"
    result = getattr(workflows, f"export_recipes_to_{kind}_result")(value)
    payload = read_export(kind, result)
    if kind == "markdown":
        assert payload["link"].endswith(recipe.uid)
    else:
        assert payload["name"] == "Synthetic soup"
    assert result.invalid_repository_entries == []


@pytest.mark.parametrize("kind", ["paprika", "tandoor", "markdown"])
@pytest.mark.parametrize("count", [0, 2])
def test_local_uid_export_keeps_selection_errors(
    monkeypatch, synthetic_recipe, kind, count
):
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult([synthetic_recipe] * count, []),
    )
    message = "Recipe not found" if count == 0 else "More than one recipe"
    with pytest.raises(workflows.UserFacingError, match=message):
        getattr(workflows, f"export_recipes_to_{kind}_result")(synthetic_recipe.uid)


@pytest.mark.parametrize("kind", ["paprika", "tandoor", "markdown", "mealie"])
@pytest.mark.parametrize("portions", [7, None])
def test_exported_portions_scale_single_serving_quantities(
    offline_exports, synthetic_recipe, kind, portions
):
    # KptnCook quantities are per serving (40 g here); exports scale them to the
    # portion count they write: the fixed count, otherwise two.
    recipe = synthetic_recipe
    recipe.fixed_portion_count = portions
    expected = portions if portions is not None else 2
    amount = 40 * expected
    if kind == "mealie":
        payload = json.loads(
            kptncook_to_mealie(recipe, api_key="synthetic").model_dump_json()
        )
        assert payload["recipe_yield"] == f"{expected} Portionen"
        assert payload["recipe_ingredient"][0]["quantity"] == amount
    elif kind == "markdown":
        [path] = MarkdownExporter().export([recipe])
        text = path.read_text(encoding="utf-8")
        assert yaml.safe_load(text.split("---", 2)[1])["servings"] == expected
        assert f"- {amount} g Flour" in text
    elif kind == "paprika":
        filename = PaprikaExporter().export([recipe])
        with ZipFile(filename) as archive:
            payload = json.loads(gzip.decompress(archive.read(archive.namelist()[0])))
        assert payload["servings"] == str(expected)
        assert payload["ingredients"] == f"{amount} g Flour\n"
    else:
        [filename] = TandoorExporter().export([recipe])
        with ZipFile(filename) as archive:
            payload = json.loads(archive.read("recipe.json"))
        assert payload["servings"] == expected
        assert payload["ingredients"][0]["amount"] == amount


def test_markdown_frontmatter_roundtrips_strings(synthetic_recipe, monkeypatch):
    monkeypatch.setattr(
        "kptncook.markdown_exporter.download_image", lambda *a, **kw: b"synthetic"
    )
    recipe = synthetic_recipe
    tags = [
        "contains: colon",
        "hash # value",
        "line\nextra: field",
        "true",
        "[list]",
        'quote " and \\ slash',
        "café",
        "soup 🍲",
        "control\x7f\x85separator\u2029",
        "line\u2028break",
    ]
    recipe.active_tags = tags
    recipe.uid = "Abc1234\nextra: injected"
    recipe.image_list = [
        Image(
            name="synthetic",
            type="cover",
            url="https://example.test/🍲#hash",
        )
    ]
    [path] = MarkdownExporter().export([recipe])
    frontmatter, body = path.read_text(encoding="utf-8").split("---", 2)[1:]
    payload = yaml.safe_load(frontmatter)
    assert payload["tags"] == tags
    assert (
        payload["link"] == f"https://mobile.kptncook.com/recipe/pinterest/{recipe.uid}"
    )
    assert payload["image"].startswith("assets/")
    assert (path.parent / payload["image"]).read_bytes() == b"synthetic"
    assert "test-key" not in frontmatter + body
    assert isinstance(payload["date"], date)
    assert payload["prepTime"] == "5m"
    assert payload["author"] == "KptnCook"
    assert set(payload) == {
        "title",
        "date",
        "servings",
        "prepTime",
        "author",
        "link",
        "image",
        "tags",
    }
    assert "- 80 g Flour" in body  # 40 g per serving, two servings
