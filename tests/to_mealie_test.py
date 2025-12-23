from kptncook.mealie import kptncook_to_mealie
from kptncook.models import Recipe


def test_parse_full_recipe(full_recipe):
    kc_recipe = Recipe.model_validate(full_recipe)
    mealie_recipe = kptncook_to_mealie(kc_recipe)
    assert mealie_recipe.name == kc_recipe.localized_title.de
    assert mealie_recipe.tags is not None
    tag_names = {tag.name for tag in mealie_recipe.tags}
    assert "kptncook" in tag_names
    assert kc_recipe.active_tags is not None
    assert set(kc_recipe.active_tags).issubset(tag_names)
    assert mealie_recipe.extras is not None
    assert "kptncook_id" in mealie_recipe.extras
    assert mealie_recipe.extras["source"] == "kptncook"

    assert mealie_recipe.nutrition is not None
    assert mealie_recipe.nutrition.calories == "900"
    assert mealie_recipe.nutrition.fatContent == "41"
    assert mealie_recipe.nutrition.proteinContent == "40"
    assert mealie_recipe.nutrition.carbohydrateContent == "85"
    assert mealie_recipe.nutrition.fiberContent is None
    assert mealie_recipe.nutrition.sodiumContent is None
    assert mealie_recipe.nutrition.sugarContent is None


def test_mealie_export_includes_active_tags(full_recipe):
    recipe_data = {**full_recipe, "activeTags": ["kptncook", "quick", "dinner"]}
    kc_recipe = Recipe.model_validate(recipe_data)

    mealie_recipe = kptncook_to_mealie(kc_recipe)

    tag_names = [tag.name for tag in mealie_recipe.tags or []]
    assert tag_names == ["kptncook", "quick", "dinner"]
