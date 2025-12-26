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


def test_parse_recipe_active_tags_present(full_recipe):
    recipe = Recipe.model_validate(full_recipe)
    assert recipe.active_tags is not None
    assert "main_ingredient_pasta" in recipe.active_tags


def test_parse_recipe_active_tags_missing(minimal):
    recipe = Recipe.model_validate(minimal)
    assert recipe.active_tags is None


def test_parse_ingredient_details_id(full_recipe):
    recipe = Recipe.model_validate(full_recipe)
    raw_id = full_recipe["ingredients"][0]["ingredient"]["_id"]["$oid"]

    assert recipe.ingredients[0].ingredient.id is not None
    assert recipe.ingredients[0].ingredient.id.oid == raw_id


def test_parse_step_ingredient_id(full_recipe):
    recipe = Recipe.model_validate(full_recipe)
    raw_steps = full_recipe["steps"]
    step_index = next(
        index for index, step in enumerate(raw_steps) if step["ingredients"]
    )
    raw_ingredient_id = raw_steps[step_index]["ingredients"][0]["ingredientId"]

    parsed_step = recipe.steps[step_index]
    assert parsed_step.ingredients is not None
    parsed_ingredient = parsed_step.ingredients[0]
    assert parsed_ingredient is not None
    assert parsed_ingredient.ingredient_id == raw_ingredient_id


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


def test_parse_recipe_with_string_fields():
    recipe_data = {
        "_id": {"$oid": "6279169b5100000701201fee"},
        "title": "Parme-sahnig und crunchy!",
        "authorComment": "Parme-sahnig und crunchy!",
        "preparationTime": 10,
        "recipeNutrition": {
            "calories": 200,
            "fat": 10,
            "carbohydrate": 20,
            "protein": 5,
        },
        "steps": [
            {
                "title": "Alles parat?",
                "ingredients": [],
                "image": {
                    "name": "REZ_0001_01.jpg",
                    "url": "https://example.com/step.jpg",
                    "type": "step",
                },
            }
        ],
        "imageList": [
            {
                "name": "REZ_0001_Cover.jpg",
                "type": "cover",
                "url": "https://example.com/cover.jpg",
            }
        ],
        "ingredients": [
            {
                "quantity": 1.0,
                "measure": "g",
                "ingredient": {
                    "_id": {"$oid": "5536511e5100000701221fee"},
                    "typ": "regular",
                    "uncountableTitle": "Pfeffer",
                    "numberTitle": "Pfeffer",
                    "category": "spices",
                },
            }
        ],
    }

    recipe = Recipe.model_validate(recipe_data)
    assert recipe.localized_title.de == "Parme-sahnig und crunchy!"
    assert recipe.author_comment.de == "Parme-sahnig und crunchy!"
    assert recipe.steps[0].title.de == "Alles parat?"
    assert recipe.ingredients[0].ingredient.localized_title.de == "Pfeffer"


def test_parse_recipe_with_number_title_strings():
    recipe_data = {
        "_id": {"$oid": "6279169b5100000701201fee"},
        "title": "Test recipe",
        "authorComment": "Author note",
        "preparationTime": 10,
        "recipeNutrition": {
            "calories": 200,
            "fat": 10,
            "carbohydrate": 20,
            "protein": 5,
        },
        "steps": [
            {
                "title": "All set?",
                "ingredients": [],
                "image": {
                    "name": "REZ_0001_01.jpg",
                    "url": "https://example.com/step.jpg",
                    "type": "step",
                },
            }
        ],
        "imageList": [
            {
                "name": "REZ_0001_Cover.jpg",
                "type": "cover",
                "url": "https://example.com/cover.jpg",
            }
        ],
        "ingredients": [
            {
                "quantity": 1.0,
                "measure": "g",
                "ingredient": {
                    "_id": {"$oid": "5536511e5100000701221fee"},
                    "typ": "regular",
                    "title": "Mandel",
                    "numberTitle": {"singular": "Mandel", "plural": "Mandeln"},
                    "category": "nuts",
                },
            }
        ],
    }

    recipe = Recipe.model_validate(recipe_data)
    assert recipe.ingredients[0].ingredient.localized_title.de == "Mandel"
