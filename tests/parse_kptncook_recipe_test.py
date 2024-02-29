from kptncook.models import IngredientDetails, Recipe, to_camel


def test_to_camel():
    assert to_camel("localized_title") == "localizedTitle"
    assert to_camel("foo") == "foo"


def test_parse_recipe_id(minimal):
    recipe = Recipe.model_validate(minimal)
    assert recipe.id.oid == minimal["_id"]["$oid"]


def test_parse_image_url(minimal):
    recipe = Recipe.model_validate(minimal)
    image_url = recipe.get_image_url("foobar")
    assert image_url is not None
    assert "foobar" in image_url

    recipe = recipe.model_copy()
    recipe.image_list = []
    assert recipe.get_image_url("foobar") is None


def test_parse_full_recipe(full_recipe):
    recipe = Recipe.model_validate(full_recipe)
    assert recipe is not None
    # from rich.pretty import pprint

    # pprint(full_recipe)
    # assert False


def test_ingredient_details_without_uncountable_title():
    ingredient_details = {
        "typ": "ingredient",
        "localizedTitle": {"de": "Zucker", "en": "sugar"},
        "numberTitle": {"de": "Zucker", "en": "sugar"},
        "uncountableTitle": None,
        "category": "baking",
    }
    ingredient_details = IngredientDetails(**ingredient_details)
    assert ingredient_details.uncountable_title == ingredient_details.number_title
