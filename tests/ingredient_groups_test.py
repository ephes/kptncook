from kptncook.config import settings
from kptncook.ingredient_groups import (
    iter_ingredient_groups,
    parse_ingredient_group_labels,
)
from kptncook.models import Recipe


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
