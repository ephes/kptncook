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


def test_mealie_export_includes_step_ingredient_references(full_recipe):
    kc_recipe = Recipe.model_validate(full_recipe)

    mealie_recipe = kptncook_to_mealie(kc_recipe)

    ingredient_reference_ids = [
        ingredient.referenceId for ingredient in mealie_recipe.recipe_ingredient or []
    ]
    assert ingredient_reference_ids
    assert all(reference_id is not None for reference_id in ingredient_reference_ids)
    assert len(set(ingredient_reference_ids)) == len(ingredient_reference_ids)

    steps_with_refs = [
        step
        for step in mealie_recipe.recipe_instructions or []
        if step.ingredientReferences
    ]
    assert steps_with_refs

    for step in steps_with_refs:
        for reference in step.ingredientReferences:
            assert reference.referenceId in ingredient_reference_ids


def test_mealie_export_handles_duplicate_ingredient_ids(minimal):
    ingredient_details = {
        "_id": {"$oid": "dup-ingredient"},
        "typ": "regular",
        "localizedTitle": {"de": "Salz"},
        "numberTitle": {"de": "Salz"},
        "uncountableTitle": {"de": "Salz"},
        "category": "spices",
    }
    recipe_data = {
        **minimal,
        "ingredients": [
            {"quantity": 1.0, "measure": "g", "ingredient": ingredient_details},
            {
                "quantity": 2.0,
                "measure": "g",
                "ingredient": {**ingredient_details},
            },
        ],
        "steps": [
            {
                "title": {"de": "All set?"},
                "ingredients": [{"ingredientId": "dup-ingredient"}],
                "image": minimal["steps"][0]["image"],
            }
        ],
    }

    kc_recipe = Recipe.model_validate(recipe_data)
    mealie_recipe = kptncook_to_mealie(kc_recipe)

    ingredient_reference_ids = [
        ingredient.referenceId for ingredient in mealie_recipe.recipe_ingredient or []
    ]
    step_reference_ids = [
        reference.referenceId
        for reference in mealie_recipe.recipe_instructions[0].ingredientReferences
    ]

    assert len(ingredient_reference_ids) == 2
    assert len(set(ingredient_reference_ids)) == 2
    assert set(step_reference_ids) == set(ingredient_reference_ids)
