"""Amounts and title fallback survive actual export rendering."""

from decimal import Decimal

import pytest

from kptncook.config import get_settings
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.models import Recipe
from kptncook.paprika import PaprikaExporter
from kptncook.tandoor import TandoorExporter


def ingredient(quantity, **titles):
    return {
        "quantity": quantity,
        "measure": "kg",
        "ingredient": {
            "typ": "regular",
            "category": "synthetic",
            **titles,
        },
    }


@pytest.mark.parametrize(
    "quantity,servings,expected",
    [
        (0.001, 2, "0.002"),
        (0.000001, 3, "0.000003"),
        (0.004999, 2, "0.009998"),
        (0.333333, 3, "0.999999"),
        (1, 2, "2"),
        (0, 4, None),
        (1.25, 2, "2.5"),
        (123456.123456, 1, "123456.123456"),
    ],
)
@pytest.mark.parametrize("unit", ["kg", "l"])
def test_markdown_preserves_admitted_quantity(
    minimal, quantity, servings, expected, unit
):
    row = ingredient(quantity, localizedTitle={"de": "Flour"})
    row["measure"] = unit
    minimal["ingredients"] = [row]
    recipe = Recipe.model_validate(minimal)
    content = MarkdownExporter(servings=servings).render_recipe(recipe)
    line = f"{expected} {unit} Flour" if expected else f"{unit} Flour"
    assert f"- {line}\n" in content
    assert (
        PaprikaExporter().format_ingredient_line(recipe.ingredients[0], servings)
        == line
    )
    amount = TandoorExporter().get_ingredients(recipe)[0]["amount"]
    # Tandoor uses the recipe's standard serving count, independently of Markdown.
    assert Decimal(str(amount)) == Decimal(str(quantity)) * recipe.servings


@pytest.mark.parametrize("lang", ["de", "en", "fr"])
@pytest.mark.parametrize(
    "uncountable,localized,number,expected",
    [
        ({}, {"en": "Flour <fine>"}, {"de": "Other"}, "Flour <fine>"),
        ({"de": "", "en": ""}, {}, {"en": "Flour <fine>"}, "Flour <fine>"),
        (None, {"en": "Other"}, {"en": "Flour <fine>"}, "Flour <fine>"),
        ({"fr": "Farine", "de": "Mehl", "en": "Flour"}, {}, {}, None),
    ],
)
def test_all_file_exporters_share_food_title_fallback(
    minimal, lang, uncountable, localized, number, expected
):
    get_settings().kptncook_lang = lang
    minimal["ingredients"] = [
        ingredient(
            1,
            uncountableTitle=uncountable,
            localizedTitle=localized,
            numberTitle=number,
        )
    ]
    recipe = Recipe.model_validate(minimal)
    expected = expected or {"de": "Mehl", "en": "Flour", "fr": "Farine"}[lang]
    assert f"- 2 kg {expected}\n" in MarkdownExporter().render_recipe(recipe)
    assert (
        PaprikaExporter().get_ingredients_text(recipe.ingredients, 2)
        == f"2 kg {expected}\n"
    )
    assert TandoorExporter().get_ingredients(recipe)[0]["food"]["name"] == expected
