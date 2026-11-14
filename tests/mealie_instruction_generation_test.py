"""An edit during the asset upload window must survive enrichment."""

import copy
import json

import httpx
import pytest

from kptncook.mealie import MealieApiClient, RecipeStep, RecipeWithImage
from kptncook.models import Image


@pytest.mark.parametrize(
    "change", ["text", "id", "order", "reference", "recipe-id", "unchanged"]
)
def test_upload_window_edit_is_not_patched(monkeypatch, caplog, change):
    source = RecipeWithImage(
        name="Invented",
        extras={"source": "kptncook", "kptncook_id": "invented"},
        recipe_instructions=[
            RecipeStep(
                text=text,
                image=Image(name="step.jpg", url="https://example.test/image"),
            )
            for text in ["Mix", "Bake"]
        ],
    )
    stored = {
        "id": "00000000-0000-4000-8000-000000000001",
        "slug": "invented",
        "name": "Invented",
        "extras": source.extras,
        "recipeInstructions": [
            {
                "id": f"step-{i}",
                "text": text,
                "serverField": i,
                "ingredientReferences": [],
            }
            for i, text in enumerate(["Mix", "Bake"])
        ],
    }
    patches, reads = [], []
    edited = None

    def handle(request):
        nonlocal edited
        if request.url.path.endswith("/about"):
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.url.path.endswith("/create/zip"):
            return httpx.Response(201, json="invented")
        if request.url.path.endswith("/assets"):
            if edited is None:
                if change == "text":
                    stored["recipeInstructions"][0]["text"] = "Human edit"
                elif change == "id":
                    stored["recipeInstructions"][0]["id"] = "new-id"
                elif change == "order":
                    stored["recipeInstructions"].reverse()
                elif change == "reference":
                    stored["recipeInstructions"][0]["ingredientReferences"] = [
                        {"referenceId": "00000000-0000-4000-8000-000000000099"}
                    ]
                elif change == "recipe-id":
                    stored["id"] = "00000000-0000-4000-8000-000000000099"
                edited = copy.deepcopy(stored)
            return httpx.Response(200, json={"fileName": "step.jpg"})
        if request.method == "PATCH":
            patches.append(json.loads(request.content))
            stored.update(patches[-1])
        else:
            reads.append(request)
        return httpx.Response(200, json=stored)

    monkeypatch.setattr(
        "kptncook.mealie.download_image", lambda *a, **kw: b"invented-image"
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        MealieApiClient("https://example.test/api", client=http).create_recipe(source)
    assert len(reads) == 2
    if change == "unchanged":
        assert len(patches) == 1
        for i, step in enumerate(patches[0]["recipeInstructions"]):
            assert step["serverField"] == i
            assert step["text"].startswith(["Mix", "Bake"][i] + " <img")
    else:
        assert patches == []
        assert stored == edited
        assert (
            "Skipping step images: recipe changed during asset uploads" in caplog.text
        )
