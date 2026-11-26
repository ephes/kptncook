"""Import receipts bind all subsequent media work to one server object."""

import json

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


@pytest.mark.parametrize(
    "change",
    [
        "slug",
        "id",
        "missing-id",
        "extras",
        "normal",
        "reordered",
        "old-steps",
        "moved-link",
        "duplicate-step-id",
        "plain-url",
        "unchanged-link",
    ],
)
def test_step_patch_reply_must_confirm_identity_and_attachments(
    monkeypatch, caplog, change
):
    url = f"/api/media/recipes/{ID_A}/assets/step.jpg"
    mix = f'Mix <img src="{url}">' if change == "unchanged-link" else "Mix"
    source = RecipeWithImage(
        name="Synthetic",
        extras={"source": "kptncook", "kptncook_id": "synthetic"},
        recipe_instructions=[
            RecipeStep(
                text=mix,
                image=Image(name="step.jpg", url="https://example.test/step.jpg"),
            ),
            RecipeStep(text="Serve"),
        ],
    )
    instructions = [{"text": mix}, {"text": "Serve"}]
    if change != "normal":
        instructions[0]["id"] = ID_A
        instructions[1]["id"] = ID_B
    stored = {
        "id": ID_A,
        "slug": "receipt-a",
        "name": "Imported",
        "extras": source.extras,
        "recipeInstructions": instructions,
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
            sent = json.loads(request.content)["recipeInstructions"]
            assert sent[0]["text"].count(url) == (
                2 if change == "unchanged-link" else 1
            )
            first, second = [dict(step) for step in instructions]
            linked = f"<p>Mix</p><IMG width='100%' SRC='{url}'>"
            if change in {"normal", "reordered", "duplicate-step-id"}:
                first["text"] = linked
            elif change == "moved-link":
                second["text"] = linked
            elif change == "plain-url":
                first["text"] = f"Mix {url}"
            if change == "duplicate-step-id":
                second["id"] = ID_A
            acknowledged = [second, first] if change == "reordered" else [first, second]
            reply = stored | {
                "name": "Acknowledged",
                "recipeInstructions": acknowledged,
            }
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
    confirmed = change in {"normal", "reordered"}
    assert caplog.text.count("Skipping step image update") == (0 if confirmed else 1)
    assert created.name == ("Acknowledged" if confirmed else "Imported")
    if confirmed:
        assert any(
            linked.text.endswith(f"SRC='{url}'>")
            for linked in created.recipe_instructions
        )
    else:
        assert [step.text for step in created.recipe_instructions] == [mix, "Serve"]
