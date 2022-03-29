from kptncook.mealie import kptncook_to_mealie
from kptncook.models import Recipe


def test_parse_full_recipe(full_recipe):
    kc_recipe = Recipe.parse_obj(full_recipe)
    mealie_recipe = kptncook_to_mealie(kc_recipe)
    assert mealie_recipe.name == kc_recipe.localized_title.de
    assert mealie_recipe.tags is not None
    assert "kptncook" in {tag.name for tag in mealie_recipe.tags}
    assert mealie_recipe.extras is not None
    assert "kptncook_id" in mealie_recipe.extras
