from kptncook.mealie import kptncook_to_mealie
from kptncook.models import Recipe


def test_parse_full_recipe(full_recipe, test_settings):
    kc_recipe = Recipe.model_validate(full_recipe)
    mealie_recipe = kptncook_to_mealie(kc_recipe, test_settings.kptncook_api_key)
    assert mealie_recipe.name == kc_recipe.localized_title.de
    assert mealie_recipe.tags is not None
    # assert "kptncook" in mealie_recipe.tags  # tags do not work atm
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
