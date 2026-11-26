"""Import receipts bind all subsequent media work to one server object."""

import httpx
import pytest

from kptncook.mealie import MealieApiClient, RecipeStep, RecipeWithImage
from kptncook.models import Image

ID_A = "11111111-1111-4111-8111-111111111111"
ID_B = "22222222-2222-4222-8222-222222222222"


@pytest.mark.parametrize("change", ["slug", "missing-id", "invalid-id"])
def test_import_detail_cannot_redirect_media(monkeypatch, change):
    source = RecipeWithImage(
        name="Synthetic",
        image_url="https://example.test/cover.jpg",
        extras={"source": "kptncook", "kptncook_id": "synthetic"},
    )
    stored = {"id": ID_A, "slug": "receipt-a", "extras": source.extras}
    if change == "slug":
        stored["slug"] = "other-b"
    elif change == "missing-id":
        stored.pop("id")
    else:
        stored["id"] = "invalid"
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/about"):
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.method == "POST":
            return httpx.Response(201, json="receipt-a")
        return httpx.Response(200, json=stored)

    monkeypatch.setattr(
        "kptncook.mealie.download_image",
        lambda *a, **kw: pytest.fail("media requested"),
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://example.test/api", client=http)
        with pytest.raises(ValueError, match="identity"):
            client.create_recipe(source)
    assert calls == [
        ("GET", "/api/app/about"),
        ("POST", "/api/recipes/create/zip"),
        ("GET", "/api/recipes/receipt-a"),
    ]


@pytest.mark.parametrize("change", ["slug", "id", "missing-id", "extras", "normal"])
def test_step_patch_reply_cannot_replace_imported_identity(monkeypatch, caplog, change):
    source = RecipeWithImage(
        name="Synthetic",
        extras={"source": "kptncook", "kptncook_id": "synthetic"},
        recipe_instructions=[
            RecipeStep(
                text="Mix",
                image=Image(name="step.jpg", url="https://example.test/step.jpg"),
            )
        ],
    )
    stored = {
        "id": ID_A,
        "slug": "receipt-a",
        "extras": source.extras,
        "recipeInstructions": [{"text": "Mix"}],
    }
    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append(request.method)
        if request.url.path.endswith("/about"):
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.url.path.endswith("/create/zip"):
            return httpx.Response(201, json="receipt-a")
        if request.url.path.endswith("/assets"):
            return httpx.Response(200, json={"fileName": "step.jpg"})
        if request.method == "PATCH":
            reply = dict(stored)
            if change == "slug":
                reply["slug"] = "other-b"
            elif change == "id":
                reply["id"] = ID_B
            elif change == "missing-id":
                reply.pop("id")
            elif change == "extras":
                reply["extras"] = {"source": "kptncook", "kptncook_id": "other"}
            return httpx.Response(200, json=reply)
        return httpx.Response(200, json=stored)

    monkeypatch.setattr(
        "kptncook.mealie.download_image", lambda *a, **kw: b"\xff\xd8\xff\xe0synthetic"
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        created = MealieApiClient(
            "https://example.test/api", client=http
        ).create_recipe(source)
    assert created.slug == "receipt-a"
    assert str(created.id) == ID_A
    assert created.extras == source.extras
    assert writes == ["POST", "POST", "PATCH"]
    assert ("Skipping step image update" in caplog.text) == (change != "normal")
