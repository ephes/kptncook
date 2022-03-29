from kptncook.models import Recipe, to_camel


def test_to_camel():
    assert to_camel("localized_title") == "localizedTitle"
    assert to_camel("foo") == "foo"


def test_parse_recipe_id(minimal):
    recipe = Recipe.parse_obj(minimal)
    assert recipe.id.oid == minimal["_id"]["$oid"]


def test_parse_image_url(minimal):
    recipe = Recipe.parse_obj(minimal)
    image_url = recipe.get_image_url("foobar")
    assert image_url is not None
    assert "foobar" in image_url

    recipe = recipe.copy()
    recipe.image_list = []
    assert recipe.get_image_url("foobar") is None


def test_parse_full_recipe(full_recipe):
    recipe = Recipe.parse_obj(full_recipe)
    assert recipe is not None
    # from rich.pretty import pprint

    # pprint(full_recipe)
    # assert False
