import httpx

from kptncook.mealie import MealieApiClient, Recipe
from kptncook.models import Image


def test_parse_empty_mealie_recipe_is_valid():
    mealie_recipe = {}
    assert Recipe.model_validate(mealie_recipe) == Recipe()


def test_parse_mealie_recipe_pydantic_update():
    """
    https://github.com/ephes/kptncook/issues/28
    This recipe raised a pydantic ValidationError after pydantic2 upgrade:
    ValidationError: 2 validation errors for Recipe
    recipe_yield
      Field required [type=missing, input_value={'id': '7fb151b5-7807-4c2... None,
    'lastMade': None}, input_type=dict]
        For further information visit https://errors.pydantic.dev/2.4/v/missing
    nutrition
      Field required [type=missing, input_value={'id': '7fb151b5-7807-4c2... None,
    'lastMade': None}, input_type=dict]
        For further information visit https://errors.pydantic.dev/2.4/v/missing
    """
    mealie_recipe = {
        "id": "9e1f19c2-a087-4834-8cbb-9722ac1e49f3",
        "userId": "fcd14f40-ecc8-4b78-9727-dc9597fc208c",
        "groupId": "c32d7dd5-75c7-4e33-9fb9-f31005b85865",
        "name": "Mediterrane Hühnchen-Quinoa-Bowl",
        "slug": "mediterrane-huhnchen-quinoa-bowl",
        "image": None,
        "recipeYield": "1 Portionen",
        "totalTime": None,
        "prepTime": "30",
        "cookTime": "0",
        "performTime": None,
        "description": "",
        "recipeCategory": [],
        "tags": [
            {
                "id": "d3709045-6618-4faf-bb2d-f9ea6435c64b",
                "name": "kptncook",
                "slug": "kptncook",
            }
        ],
        "tools": [],
        "rating": None,
        "orgURL": None,
        "dateAdded": None,
        "dateUpdated": "2022-12-13T18:21:22.189956",
        "createdAt": None,
        "updateAt": None,
        "lastMade": None,
    }
    recipe = Recipe.model_validate(mealie_recipe)
    assert recipe.name == mealie_recipe["name"]


def test_skip_validation_errors():
    invalid_recipes = [
        {"id": "123", "nutrition": "not a nutrition dict"},
    ]
    assert MealieApiClient.validate_recipes(invalid_recipes) == []


def test_upload_asset_follows_redirects(monkeypatch):
    client = MealieApiClient("http://mealie.local/api")
    image = Image(name="step.jpg", url="http://images.kptncook.com/step.jpg")
    seen = {}

    def fake_get(url, follow_redirects=False, **kwargs):
        seen["follow_redirects"] = follow_redirects
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, content=b"image-bytes")

    def fake_post(path, data=None, files=None, **kwargs):
        request = httpx.Request("POST", f"http://mealie.local/api{path}")
        return httpx.Response(
            200,
            request=request,
            json={"fileName": "step.jpg", "name": "step", "icon": "mdi-file-image"},
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    client.post = fake_post

    result = client.upload_asset("recipe-slug", image)

    assert seen["follow_redirects"] is True
    assert result["fileName"] == "step.jpg"
