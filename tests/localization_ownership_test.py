"""Five-language synthetic records and caller-owned normalization inputs."""

import copy
import json

import pytest
import yaml
from pydantic import ValidationError

from kptncook.config import settings
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.mealie import kptncook_to_mealie
from kptncook.models import Recipe, LocalizedString, localized_fallback
from kptncook.paprika import PaprikaExporter, GeneratedData
from kptncook.tandoor import TandoorExporter


def localized(field):
    return {lang: f"{lang} {field}" for lang in ("en", "fr", "de", "es", "pt")}


def normalization_payload(minimal):
    raw = copy.deepcopy(minimal)
    raw.pop("localizedTitle")
    raw["name"] = localized("title")
    raw["authorComment"] = localized("comment")
    raw["ingredients"] = [
        {
            "quantity": 1,
            "ingredient": {
                "typ": "regular",
                "category": "test",
                "name": localized("flour"),
            },
        }
    ]
    raw["steps"][0].update(
        title=localized("step"),
        ingredients=[
            {
                "amount": 2,
                "measure": "g",
                "ingredient": {
                    "_id": {"$oid": "flour"},
                    "title": localized("flour"),
                    "numberTitle": localized("flours"),
                },
            }
        ],
    )
    return raw


@pytest.mark.parametrize("invalid", [False, True])
def test_validation_does_not_mutate_nested_caller_payload(minimal, invalid):
    raw = normalization_payload(minimal)
    if invalid:
        raw["preparationTime"] = "invalid"
    before = copy.deepcopy(raw)
    for _ in range(2):
        if invalid:
            with pytest.raises(ValidationError):
                Recipe.model_validate(raw)
        else:
            recipe = Recipe.model_validate(raw)
            assert recipe.localized_title.en == "en title"
            assert recipe.steps[0].ingredients[0].quantity == 2
            assert recipe.steps[0].ingredients[0].ingredient_id == "flour"
            assert recipe.ingredients[0].ingredient.number_title.en == "en flour"
        assert raw == before


@pytest.mark.parametrize("lang", ["en", "fr", "de", "es", "pt", " fr "])
def test_configured_language_across_exporters(minimal, monkeypatch, lang):
    monkeypatch.setattr(settings, "kptncook_lang", lang)
    lang = lang.strip()
    raw = normalization_payload(minimal)
    recipe = Recipe.model_validate(raw)
    before = copy.deepcopy(recipe)
    monkeypatch.setattr(
        PaprikaExporter,
        "get_generated_data",
        lambda *a, **kw: GeneratedData(None, None, "synthetic", "synthetic"),
    )
    paprika = json.loads(PaprikaExporter().get_recipe_as_json_string(recipe))
    assert paprika["name"] == f"{lang} title"
    assert f"{lang} flour" in paprika["ingredients"]
    assert f"{lang} step" in paprika["directions"]
    tandoor = TandoorExporter().get_recipe_payload(recipe)
    assert tandoor["name"] == f"{lang} title"
    assert tandoor["description"] == f"{lang} comment"
    assert f"{lang} step" in str(tandoor)
    assert f"{lang} flour" in str(tandoor)
    mealie = kptncook_to_mealie(recipe, api_key="synthetic")
    assert mealie.name == f"{lang} title"
    assert mealie.recipe_instructions[0].text == f"{lang} step"
    assert mealie.recipe_ingredient[0].food.name == f"{lang} flour"
    markdown = MarkdownExporter().render_recipe(recipe)
    assert f"{lang} comment" in markdown
    assert f"{lang} step" in markdown
    assert f"{lang} flour" in markdown
    assert recipe == before


@pytest.mark.parametrize(
    "lang, translations, expected",
    [
        ("fr", {"fr": "", "de": "German", "en": "English"}, "German"),
        ("pt", {"en": "English"}, "English"),
        ("xx", {"de": "German", "en": "English"}, "English"),
        ("fr", {}, None),
        ("fr", {"fr": "\t", "de": " ", "en": "\n"}, None),
    ],
)
def test_missing_and_empty_language_fallback(monkeypatch, lang, translations, expected):
    monkeypatch.setattr(settings, "kptncook_lang", lang)
    assert localized_fallback(LocalizedString(**translations), lang) == expected


def test_blank_recipe_title_falls_back_in_markdown(minimal, monkeypatch):
    monkeypatch.setattr(settings, "kptncook_lang", "fr")
    title = "  German title  "
    minimal["localizedTitle"] = {"fr": "\t\u2003\n", "de": title, "en": "English title"}
    recipe = Recipe.model_validate(minimal)

    markdown = MarkdownExporter().render_recipe(recipe)
    assert yaml.safe_load(markdown.split("---", 2)[1])["title"] == title
    assert recipe.localized_title.fallback("fr") == title


@pytest.mark.parametrize(
    "localized_unit, expected",
    [
        ({"singular": {}, "plural": {"de": "Esslöffel"}}, "Esslöffel"),
        (
            {"singular": {"de": "\u2003"}, "plural": {"de": " Esslöffel "}},
            " Esslöffel ",
        ),
        ({"singular": {"it": "cucchiaio"}, "plural": {"de": "Esslöffel"}}, "Esslöffel"),
        ({"singular": {"en": "tbsp"}, "plural": {"de": "Esslöffel"}}, "tbsp"),
        ({"singular": {"de": None}, "de": "EL"}, "EL"),
    ],
)
def test_legacy_step_unit_uses_first_usable_variant(
    minimal, monkeypatch, localized_unit, expected
):
    monkeypatch.setattr(settings, "kptncook_lang", "de")
    minimal["steps"][0]["ingredients"] = [
        {"ingredient": "Mehl", "unit": {"localizedTitle": localized_unit}}
    ]
    recipe = Recipe.model_validate(minimal)
    payload = TandoorExporter().get_recipe_payload(recipe)
    assert payload["steps"][0]["ingredients"][0]["unit"] == {"name": expected}


def test_model_localization_does_not_load_configuration(monkeypatch):
    def fail():
        pytest.fail("Pure localization loaded settings")

    monkeypatch.setattr("kptncook.config.get_settings", fail)
    monkeypatch.setattr("kptncook.models.get_settings", fail, raising=False)
    value = LocalizedString(de="German", en="English")
    assert localized_fallback(value) == "German"
    assert localized_fallback(value, "en") == "English"
