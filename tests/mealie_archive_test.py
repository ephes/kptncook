"""HTTP-boundary regressions for native Mealie archive creation."""

import io
import json
from email.parser import BytesParser
from email.policy import default
from zipfile import ZipFile

import httpx
import pytest

from kptncook.mealie import (
    MealieApiClient,
    RecipeWithImage,
    RecipeStep,
    Recipe,
    RecipeCategory,
    RecipeIngredient,
    RecipeFood,
    RecipeUnit,
    RecipeTag,
)
from kptncook.models import Image


def archive_payload(request):
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: "
        + request.headers["content-type"].encode()
        + b"\r\n\r\n"
        + request.content
    )
    parts = list(message.iter_parts())
    assert len(parts) == 1
    assert parts[0].get_param("name", header="content-disposition") == "archive"
    with ZipFile(io.BytesIO(parts[0].get_payload(decode=True))) as archive:
        assert archive.namelist() == ["recipe.json"]
        return json.loads(archive.read("recipe.json"))


@pytest.mark.parametrize(
    ("version", "endpoint"),
    [
        ("v1.12.0", "/api/recipes/create-from-zip"),
        ("v3.28.0", "/api/recipes/create/zip"),
    ],
)
def test_archive_import_has_identity_and_preserves_assigned_name(version, endpoint):
    calls = []
    stored = {}

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/app/about":
            return httpx.Response(200, json={"version": version})
        if request.method == "POST" and request.url.path == endpoint:
            stored.update(archive_payload(request))
            assert stored["extras"] == {
                "source": "kptncook",
                "kptncook_id": "second-id",
            }
            assert "id" not in stored
            assert "user_id" not in stored
            assert "group_id" not in stored
            assert "slug" not in stored
            assert "image_url" not in stored
            assert "image" not in stored["recipe_instructions"][0]
            return httpx.Response(201, json="same-title-1")
        if request.method == "GET" and request.url.path == "/api/recipes/same-title-1":
            return httpx.Response(
                200,
                json={
                    "id": "ac49c559-a816-4a4e-95ca-bea4c21d71bc",
                    "name": "Same title (1)",
                    "slug": "same-title-1",
                    "extras": stored["extras"] | {"group_id": "owner"},
                    "recipeInstructions": [{"text": "Mix", "ingredientReferences": []}],
                    "recipeIngredient": [],
                },
            )
        pytest.fail(f"Unexpected mutation/request: {request.method} {request.url}")

    recipe = RecipeWithImage(
        name="Same title",
        slug="stale-source-slug",
        id="657d0eb6-b70d-4f50-9c5b-fb9551b2cbef",
        recipe_instructions=[RecipeStep(text="Mix")],
        extras={"source": "kptncook", "kptncook_id": "second-id"},
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("http://mealie.local/api", client=http)
        result = client.create_recipe(recipe)
    assert result.name == "Same title (1)"
    assert result.slug == "same-title-1"
    assert result.recipe_instructions[0].text == "Mix"
    assert result.extras.items() >= recipe.extras.items()
    assert len([call for call in calls if call[0] == "POST"]) == 1
    assert stored["recipe_instructions"][0]["text"] == "Mix"


@pytest.mark.parametrize("failure", ["timeout", "bad-json", "bad-recipe"])
def test_ambiguous_archive_outcome_never_retries_or_deletes(failure):
    calls = []

    def handle(request):
        calls.append(request.method)
        if request.url.path == "/api/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.method == "POST":
            archive_payload(request)
            if failure == "timeout":
                raise httpx.ReadTimeout("committed before timeout", request=request)
            if failure == "bad-json":
                return httpx.Response(201, content=b"invalid json")
            return httpx.Response(201, json="created-slug")
        if request.method == "GET":
            return httpx.Response(200, json={"nutrition": "invalid"})
        pytest.fail("An ambiguous outcome must not delete or retry")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("http://mealie.local/api", client=http)
        with pytest.raises((httpx.ReadTimeout, ValueError)):
            client.create_recipe(
                RecipeWithImage(name="Test", extras={"kptncook_id": "id"})
            )
    assert calls.count("POST") == 1
    assert "DELETE" not in calls


@pytest.mark.parametrize(
    "failed_operation", ["cover", "asset", "patch", "malformed-asset"]
)
def test_media_failures_leave_core_import_successful(failed_operation):
    calls = []
    instruction = {
        "id": "027cd7bf-7c36-44c1-a4d2-970e5e3fdabc",
        "text": "Mix",
        "title": "Preparation",
        "ingredientReferences": [],
    }

    def handle(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/api/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        if path == "/api/recipes/create/zip":
            archive_payload(request)
            return httpx.Response(201, json="test-1")
        if request.method == "GET" and path == "/api/recipes/test-1":
            return httpx.Response(
                200,
                json={
                    "id": "ac49c559-a816-4a4e-95ca-bea4c21d71bc",
                    "name": "Test (1)",
                    "slug": "test-1",
                    "extras": {"source": "kptncook", "kptncook_id": "id"},
                    "recipeInstructions": [instruction],
                    "assets": [],
                },
            )
        if path.endswith("/image"):
            return httpx.Response(500 if failed_operation == "cover" else 200, json={})
        if path.endswith("/assets"):
            if failed_operation == "asset":
                return httpx.Response(500)
            if failed_operation == "malformed-asset":
                return httpx.Response(200, json={})
            return httpx.Response(
                200,
                json={"fileName": "step.jpg", "name": "step", "icon": "mdi-file-image"},
            )
        if request.method == "PATCH":
            payload = json.loads(request.content)
            assert set(payload) == {"recipeInstructions"}
            patched = payload["recipeInstructions"][0]
            assert patched["id"] == instruction["id"]
            assert patched["title"] == "Preparation"
            assert patched["text"].startswith("Mix <img")
            assert (
                "ac49c559-a816-4a4e-95ca-bea4c21d71bc/assets/step.jpg"
                in patched["text"]
            )
            return httpx.Response(
                500 if failed_operation == "patch" else 200,
                json={
                    "name": "Test (1)",
                    "slug": "test-1",
                    "recipeInstructions": [patched],
                    "extras": {"source": "kptncook", "kptncook_id": "id"},
                },
            )
        pytest.fail(f"Unexpected {request.method} {path}")

    recipe = RecipeWithImage(
        name="Test",
        image_url="https://images.example/cover.jpg",
        extras={"source": "kptncook", "kptncook_id": "id"},
        recipe_instructions=[
            RecipeStep(
                text="Mix",
                image=Image(name="step.jpg", url="https://images.example/step.jpg"),
            )
        ],
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("http://mealie.local/api", client=http)
        # Only the external image download is replaced; Mealie HTTP remains real.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                httpx,
                "get",
                lambda *a, **kw: httpx.Response(
                    200,
                    content=b"image",
                    request=httpx.Request("GET", "https://images.example/step.jpg"),
                ),
            )
            result = client.create_recipe(recipe)
    assert result.slug == "test-1"
    assert result.extras["kptncook_id"] == "id"
    assert not any(method in {"DELETE", "PUT"} for method, _ in calls)
    if failed_operation in {"asset", "malformed-asset"}:
        assert not any(method == "PATCH" for method, _ in calls)


@pytest.mark.parametrize(
    "extras", [{}, {"source": "kptncook", "kptncook_id": "wrong-id"}]
)
def test_import_response_without_matching_identity_is_reported_not_deleted(extras):
    calls = []

    def handle(request):
        calls.append(request.method)
        if request.url.path == "/api/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.method == "POST":
            archive_payload(request)
            return httpx.Response(201, json="test")
        if request.method == "GET":
            return httpx.Response(200, json={"slug": "test", "extras": extras})
        pytest.fail("Missing identity must not cause destructive rollback")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("http://mealie.local/api", client=http)
        with pytest.raises(ValueError, match="identity"):
            client.create_recipe(
                RecipeWithImage(
                    name="Test",
                    extras={"source": "kptncook", "kptncook_id": "expected-id"},
                )
            )
    assert calls.count("POST") == 1
    assert "DELETE" not in calls


def test_categorized_recipe_remains_in_identity_inventory():
    raw = {
        "id": "e7155bb5-968e-441d-98cd-cdc0bc2b336d",
        "name": "Categorized",
        "slug": "categorized",
        "recipeCategory": [
            {
                "id": "8ef9b0d1-1ee2-435a-8ad0-0e80e86a6325",
                "name": "Dinner",
                "slug": "dinner",
            }
        ],
        "extras": {"source": "kptncook", "kptncook_id": "stored-id"},
    }
    recipes = MealieApiClient.validate_recipes([raw])
    assert len(recipes) == 1
    recipe = recipes[0]
    assert recipe.extras["kptncook_id"] == "stored-id"
    assert isinstance(recipe.recipe_category[0], RecipeCategory)
    assert recipe.recipe_category[0].name == "Dinner"
    assert recipe.model_dump(mode="json")["recipe_category"][0]["slug"] == "dinner"
    assert (
        recipe.model_dump(mode="json", by_alias=True)["recipeCategory"][0]["slug"]
        == "dinner"
    )
    assert Recipe(recipe_category=["Dinner"]).recipe_category == ["Dinner"]


@pytest.mark.parametrize("about_status, version", [(503, "v3.28.0"), (200, "invalid")])
def test_version_discovery_failure_does_not_mutate_entities(about_status, version):
    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append((request.method, request.url.path))
            return httpx.Response(
                201,
                json={
                    "id": "ec426fd2-fc76-4870-b1ae-18e9afde1295",
                    "name": json.loads(request.content)["name"],
                },
            )
        if request.url.path == "/api/app/about":
            return httpx.Response(about_status, json={"version": version})
        return httpx.Response(200, json={"items": [], "total_pages": 1})

    recipe = RecipeWithImage(
        name="Test",
        tags=[RecipeTag(name="new tag")],
        recipe_ingredient=[
            RecipeIngredient(
                unit=RecipeUnit(name="spoon"), food=RecipeFood(name="flour")
            )
        ],
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("http://mealie.local/api", client=http)
        with pytest.raises((httpx.HTTPStatusError, ValueError)):
            client.create_recipe(recipe)
    assert writes == []
