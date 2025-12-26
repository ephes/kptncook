import httpx
import pytest

import kptncook
from kptncook import _extract_mealie_detail_message
from kptncook.config import settings
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


def test_get_mealie_client_uses_token(monkeypatch):
    called = {}

    class FakeClient:
        def __init__(self, base_url):
            self.base_url = base_url

        def login_with_token(self, token):
            called["token"] = token

        def login(self, username, password):
            called["login"] = (username, password)

    monkeypatch.setattr(kptncook, "MealieApiClient", FakeClient)
    monkeypatch.setattr(settings, "mealie_api_token", "token-123")
    monkeypatch.setattr(settings, "mealie_username", "user")
    monkeypatch.setattr(settings, "mealie_password", "pass")

    client = kptncook.get_mealie_client()

    assert isinstance(client, FakeClient)
    assert called == {"token": "token-123"}


def test_get_mealie_client_uses_username_password(monkeypatch):
    called = {}

    class FakeClient:
        def __init__(self, base_url):
            self.base_url = base_url

        def login_with_token(self, token):
            called["token"] = token

        def login(self, username, password):
            called["login"] = (username, password)

    monkeypatch.setattr(kptncook, "MealieApiClient", FakeClient)
    monkeypatch.setattr(settings, "mealie_api_token", None)
    monkeypatch.setattr(settings, "mealie_username", "user")
    monkeypatch.setattr(settings, "mealie_password", "pass")

    client = kptncook.get_mealie_client()

    assert isinstance(client, FakeClient)
    assert called == {"login": ("user", "pass")}


def test_get_mealie_client_exits_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "mealie_api_token", None)
    monkeypatch.setattr(settings, "mealie_username", None)
    monkeypatch.setattr(settings, "mealie_password", None)

    with pytest.raises(SystemExit):
        kptncook.get_mealie_client()


def test_extract_mealie_detail_message_handles_list_response():
    request = httpx.Request("PUT", "http://mealie.local/api/recipes/slug")
    response = httpx.Response(
        422,
        request=request,
        json=[{"loc": ["body", "image_url"], "msg": "missing", "type": "value_error"}],
        headers={"content-type": "application/json"},
    )

    assert _extract_mealie_detail_message(response) is None


def test_extract_mealie_detail_message_reads_detail_message():
    request = httpx.Request("PUT", "http://mealie.local/api/recipes/slug")
    response = httpx.Response(
        409,
        request=request,
        json={"detail": {"message": "Recipe already exists"}},
        headers={"content-type": "application/json"},
    )

    assert _extract_mealie_detail_message(response) == "Recipe already exists"
