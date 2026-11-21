import pytest

import kptncook.ingredient_groups as grouping
from kptncook.config import settings
from kptncook.ingredient_groups import (
    iter_ingredient_groups,
    parse_ingredient_group_labels,
)
from kptncook.models import Ingredient, Recipe


def test_parse_ingredient_group_labels_overrides_defaults():
    labels = parse_ingredient_group_labels("regular:Need,basic:Pantry,other:Misc")
    assert labels["regular"] == "Need"
    assert labels["basic"] == "Pantry"
    assert labels["other"] == "Misc"


def test_iter_ingredient_groups_disabled(full_recipe, monkeypatch):
    recipe = Recipe.model_validate(full_recipe)
    monkeypatch.setattr(settings, "kptncook_group_ingredients_by_typ", False)
    monkeypatch.setattr(settings, "kptncook_ingredient_group_labels", None)

    groups = iter_ingredient_groups(recipe.ingredients)

    assert len(groups) == 1
    label, ingredients = groups[0]
    assert label is None
    # When grouping is disabled, ingredients are returned untouched, in original order
    assert ingredients == recipe.ingredients


def test_iter_ingredient_groups_enabled(full_recipe, monkeypatch):
    recipe = Recipe.model_validate(full_recipe)
    monkeypatch.setattr(settings, "kptncook_group_ingredients_by_typ", True)
    monkeypatch.setattr(
        settings, "kptncook_ingredient_group_labels", "regular:Need,basic:Pantry"
    )

    groups = iter_ingredient_groups(recipe.ingredients)

    labels = [label for label, _ in groups]
    assert labels[:2] == ["Need", "Pantry"]
    assert any(ingredient.ingredient.typ == "regular" for ingredient in groups[0][1])
    assert any(ingredient.ingredient.typ == "basic" for ingredient in groups[1][1])


@pytest.mark.parametrize("count", [256, 512, 1024])
def test_distinct_group_membership_has_linear_comparisons(monkeypatch, count):
    comparisons = 0

    class CountedKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            nonlocal comparisons
            comparisons += 1
            return super().__eq__(other)

    original_key = grouping._ingredient_group_key
    monkeypatch.setattr(
        grouping, "_ingredient_group_key", lambda item: CountedKey(original_key(item))
    )
    monkeypatch.setattr(settings, "kptncook_group_ingredients_by_typ", True)
    ingredients = [
        Ingredient.model_validate(
            {
                "ingredient": {
                    "typ": f"unknown_{i}",
                    "localizedTitle": {},
                    "numberTitle": {},
                    "category": "test",
                }
            }
        )
        for i in range(count)
    ]

    groups = iter_ingredient_groups(ingredients)

    assert [items[0] for _, items in groups] == ingredients
    assert comparisons <= 10 * count


def test_mixed_groups_preserve_label_priority_and_first_seen_order(monkeypatch):
    monkeypatch.setattr(settings, "kptncook_group_ingredients_by_typ", True)
    monkeypatch.setattr(
        settings,
        "kptncook_ingredient_group_labels",
        "basic:Vorrat,extra:Extras, BASIC : Vorrat, EXTRA : Extras",
    )
    ingredients = [
        Ingredient.model_validate(
            {
                "ingredient": {
                    "typ": typ,
                    "localizedTitle": {},
                    "numberTitle": {},
                    "category": "test",
                }
            }
        )
        for typ in ["zebra", "basic", "alpha", "regular", "extra", "zebra", " "]
    ]

    groups = iter_ingredient_groups(ingredients)

    assert [label for label, _ in groups] == [
        "You need",
        "Vorrat",
        "Extras",
        "Zebra",
        "Alpha",
        "Other",
    ]
    assert [items for _, items in groups] == [
        [ingredients[3]],
        [ingredients[1]],
        [ingredients[4]],
        [ingredients[0], ingredients[5]],
        [ingredients[2]],
        [ingredients[6]],
    ]
