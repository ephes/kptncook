"""Synthetic HTTP import responses cannot redirect auth or misassociate images."""

import copy
import json

import httpx
import pytest

from kptncook.mealie import (
    MealieApiClient,
    MealieInventoryError,
    RecipeStep,
    RecipeWithImage,
)
from kptncook.models import Image

UNSAFE = [
    "../users",
    "a/b",
    "a\\b",
    "a?x=1",
    "a#part",
    "%2fusers",
    "%252fusers",
    ".",
    "..",
    "",
    "nul\0slug",
]


@pytest.mark.parametrize("slug", UNSAFE)
@pytest.mark.parametrize("operation", ["get", "delete", "asset", "cover"])
def test_consumer_slug_refused_before_authenticated_request(
    monkeypatch, slug, operation
):
    requests = []
    monkeypatch.setattr(
        "kptncook.mealie.download_image",
        lambda *a, **kw: pytest.fail("invalid route downloaded media"),
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: requests.append(req) or httpx.Response(200, json={})
        )
    ) as http:
        client = MealieApiClient("https://example.test/api", client=http)
        client.login_with_token("synthetic-bearer")
        with pytest.raises(ValueError, match="slug"):
            if operation == "get":
                client.get_via_slug(slug)
            elif operation == "delete":
                client.delete_via_slug(slug)
            elif operation == "asset":
                client.upload_asset(
                    slug, Image(name="image.jpg", url="https://example.test/image")
                )
            else:
                client._scrape_image_for_recipe(
                    RecipeWithImage(
                        name="Synthetic", image_url="https://example.test/image"
                    ),
                    slug,
                )
    assert requests == []


@pytest.mark.parametrize("slug", UNSAFE)
@pytest.mark.parametrize("where", ["archive", "detail"])
def test_server_slug_refused_before_followup_writes(slug, where):
    requests = []

    def handle(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.method == "POST":
            return httpx.Response(201, json=slug if where == "archive" else "safe")
        return httpx.Response(
            200,
            json={
                "name": "Synthetic",
                "slug": slug,
                "extras": {"source": "kptncook", "kptncook_id": "synthetic-id"},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://example.test/api", client=http)
        client.login_with_token("synthetic-bearer")
        with pytest.raises(ValueError, match="slug"):
            client.create_recipe(
                RecipeWithImage(
                    name="Synthetic",
                    image_url="https://example.test/image",
                    extras={"source": "kptncook", "kptncook_id": "synthetic-id"},
                )
            )
    assert requests == [
        ("GET", "/api/app/about"),
        ("POST", "/api/recipes/create/zip"),
    ] + ([("GET", "/api/recipes/safe")] if where == "detail" else [])


@pytest.mark.parametrize("slug", ["ordinary-hyphen", "crème-brûlée", "豆腐_スープ"])
def test_valid_unicode_slug_remains_one_route_component(slug):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"name": "Synthetic", "slug": slug})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = MealieApiClient("https://example.test/api", client=http).get_via_slug(
            slug
        )
    assert result.slug == slug
    assert requests[0].url.path == f"/api/recipes/{slug}"
    assert requests[0].url.query == b""


@pytest.mark.parametrize(
    "change", ["normal", "omitted", "reordered", "inserted", "repeated"]
)
def test_images_require_unique_exact_step_correspondence(monkeypatch, caplog, change):
    texts = ["Mix synthetic flour", "Bake synthetic bread"]
    if change == "repeated":
        texts = ["Mix", "Mix"]
    source = RecipeWithImage(
        name="Synthetic",
        extras={"source": "kptncook", "kptncook_id": "synthetic-id"},
        recipe_instructions=[
            RecipeStep(
                text=text,
                image=Image(
                    name=f"image-{index}.jpg", url=f"https://example.test/image-{index}"
                ),
            )
            for index, text in enumerate(texts)
        ],
    )
    instructions = [
        {
            "id": f"00000000-0000-4000-8000-00000000000{index}",
            "text": text,
            "title": "server-title",
            "ingredientReferences": [],
            "serverOnly": index,
        }
        for index, text in enumerate(texts)
    ]
    if change == "omitted":
        instructions.pop()
    elif change == "reordered":
        instructions.reverse()
    elif change == "inserted":
        instructions.insert(0, {"text": "Unexpected"})
    before = copy.deepcopy(instructions)
    requests = []
    patches = []
    stored = {
        "id": "00000000-0000-4000-8000-000000000099",
        "name": "Synthetic",
        "slug": "synthetic",
        "extras": source.extras,
        "recipeInstructions": instructions,
    }

    def handle(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.url.path == "/api/recipes/create/zip":
            return httpx.Response(201, json="synthetic")
        if request.url.path.endswith("/assets"):
            return httpx.Response(200, json={"fileName": "image.jpg"})
        if request.method == "PATCH":
            patches.append(json.loads(request.content))
            return httpx.Response(200, json={**stored, **patches[-1]})
        return httpx.Response(200, json=stored)

    monkeypatch.setattr(
        "kptncook.mealie.download_image", lambda *a, **kw: b"synthetic-image"
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        created = MealieApiClient(
            "https://example.test/api", client=http
        ).create_recipe(source)
    uploads = [request for request in requests if request[1].endswith("/assets")]
    assert created.name == "Synthetic"
    if change == "normal":
        assert len(uploads) == 2
        assert len(patches) == 1
        assert set(patches[0]) == {"recipeInstructions"}
        for index, step in enumerate(patches[0]["recipeInstructions"]):
            assert step["text"].startswith(texts[index] + " <img")
            assert {key: value for key, value in step.items() if key != "text"} == {
                key: value for key, value in before[index].items() if key != "text"
            }
    else:
        assert uploads == []
        assert patches == []
        assert [step.text for step in created.recipe_instructions] == [
            step["text"] for step in before
        ]
        assert "Skipping step images" in caplog.text
        assert "https://example.test/image" not in caplog.text
    assert source.recipe_instructions[0].text == texts[0]


@pytest.mark.parametrize("slug", UNSAFE)
def test_inventory_slug_is_refused_with_private_position(slug):
    with pytest.raises(MealieInventoryError, match="position 1") as error:
        MealieApiClient.validate_recipes([{"name": "Synthetic", "slug": slug}])
    assert (
        str(error.value)
        == "Incomplete Mealie inventory: unreadable identity at position 1"
    )
