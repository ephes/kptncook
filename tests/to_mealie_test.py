from kptncook.mealie import kptncook_to_mealie
from kptncook.models import Recipe


def test_parse_full_recipe(full_recipe):
    kc_recipe = Recipe.parse_obj(full_recipe)
    mealie_recipe = kptncook_to_mealie(kc_recipe)
    assert mealie_recipe.name == kc_recipe.localized_title.de
