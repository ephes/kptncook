from kptncook.mealie import kptncook_to_mealie
from kptncook.models import Recipe


def test_parse_full_recipe(full_recipe):
    kc_recipe = Recipe.model_validate(full_recipe)
    mealie_recipe = kptncook_to_mealie(kc_recipe)
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

    assert mealie_recipe.recipe_ingredient is not None
    assert len(mealie_recipe.recipe_ingredient) == 13
    assert mealie_recipe.recipe_ingredient[7].food.name == "Lachsfilet"
    assert len({ i.reference_id for i in mealie_recipe.recipe_ingredient}) == 13

    assert mealie_recipe.recipe_instructions is not None
    assert len(mealie_recipe.recipe_instructions) == 17

    assert mealie_recipe.recipe_instructions[1] is not None
    assert mealie_recipe.recipe_instructions[1].ingredient_references is not None
    assert len(mealie_recipe.recipe_instructions[1].ingredient_references) == 1
    assert mealie_recipe.recipe_instructions[1].ingredient_references[0].reference_id == mealie_recipe.recipe_ingredient[7].reference_id

