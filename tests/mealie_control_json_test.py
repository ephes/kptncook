"""Control responses are tested as bytes so duplicate members survive fixtures."""

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


@pytest.mark.parametrize(
    "duplicate",
    [
        '"id":null,"id":null,',
        '"slug":"other","slug":"invented",',
        '"recipeInstructions":[],"recipeInstructions":[{"text":"Mix"}],',
        '"recipeInstructions":[{"text":"Changed","text":"Mix"}],',
        '"sl\\u0075g":"other","slug":"invented",',
    ],
)
def test_duplicate_import_detail_prevents_media_writes(duplicate, monkeypatch):
    requests = []

    def handle(request):
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/about"):
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.method == "POST":
            return httpx.Response(201, json="invented")
        defaults = {
            "name": "Invented",
            "extras": {"source": "kptncook", "kptncook_id": "invented"},
        }
        if "slug" not in duplicate and "sl\\u0075g" not in duplicate:
            defaults["slug"] = "invented"
        if "recipeInstructions" not in duplicate:
            defaults["recipeInstructions"] = [{"text": "Mix"}]
        body = "{" + duplicate + json.dumps(defaults)[1:]
        return httpx.Response(200, content=body)

    monkeypatch.setattr(
        "kptncook.mealie.download_image",
        lambda *a, **kw: pytest.fail("Malformed detail downloaded an image"),
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://example.test/api", client=http)
        with pytest.raises(ValueError, match="JSON"):
            client.create_recipe(
                RecipeWithImage(
                    name="Invented",
                    extras={"source": "kptncook", "kptncook_id": "invented"},
                    recipe_instructions=[
                        RecipeStep(
                            text="Mix",
                            image=Image(
                                name="step.jpg", url="https://example.test/image"
                            ),
                        )
                    ],
                )
            )
    assert requests == [
        ("GET", "/api/app/about"),
        ("POST", "/api/recipes/create/zip"),
        ("GET", "/api/recipes/invented"),
    ]


@pytest.mark.parametrize(
    "operation,body,message",
    [
        (
            "login",
            '{"access_token":"private-one","access_token":"private-two"}',
            "login response",
        ),
        ("inventory", '{"items":[],"items":[],"total_pages":0}', "inventory"),
        ("detail", '{"slug":"other","slug":"invented"}', "inventory"),
        ("about", '{"version":"v1.12.0","version":"v3.28.0"}', "JSON"),
    ],
)
def test_duplicate_control_reads_refused_without_secret(operation, body, message):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    ) as http:
        client = MealieApiClient("https://example.test/api", client=http)
        client.login_with_token("prior-token")
        with pytest.raises(ValueError, match=message) as error:
            if operation == "login":
                client.login("invented", "invented-password")
            elif operation == "inventory":
                client.get_all_recipes()
            elif operation == "detail":
                client.get_via_slug("invented")
            else:
                client._get_archive_endpoint()
        assert "private-" not in str(error.value)
        assert client.headers["authorization"] == "Bearer prior-token"
        assert error.value.__suppress_context__


def test_array_repeated_values_are_allowed():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "slug": "invented",
                    "recipeInstructions": [{"text": "Mix"}, {"text": "Mix"}],
                },
            )
        )
    ) as http:
        recipe = MealieApiClient("https://example.test/api", client=http).get_via_slug(
            "invented"
        )
        assert [step.text for step in recipe.recipe_instructions] == ["Mix", "Mix"]


def test_deep_control_json_has_domain_error():
    body = "[" * 2000 + "0" + "]" * 2000
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    ) as http:
        with pytest.raises(MealieInventoryError, match="inventory"):
            MealieApiClient("https://example.test/api", client=http).get_all_recipes()


@pytest.mark.parametrize("boundary", ["asset", "reread", "patch"])
def test_duplicate_enrichment_response_does_not_retry_or_leak(
    monkeypatch, caplog, boundary
):
    calls, patches = [], []
    stored = {
        "name": "Invented",
        "slug": "invented",
        "recipeInstructions": [{"text": "Mix"}],
    }
    reads = 0

    def handle(request):
        nonlocal reads
        calls.append(request.method)
        if request.url.path.endswith("/about"):
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.url.path.endswith("/create/zip"):
            return httpx.Response(201, json="invented")
        if request.url.path.endswith("/assets"):
            return (
                httpx.Response(
                    200, content='{"fileName":"private-fragment","fileName":"step.jpg"}'
                )
                if boundary == "asset"
                else httpx.Response(200, json={"fileName": "step.jpg"})
            )
        if request.method == "PATCH":
            patches.append(request)
            return httpx.Response(
                200, content='{"slug":"private-fragment","slug":"invented"}'
            )
        reads += 1
        if reads == 2 and boundary == "reread":
            return httpx.Response(
                200,
                content='{"slug":"private-fragment","slug":"invented","name":"Invented","recipeInstructions":[{"text":"Mix"}]}',
            )
        return httpx.Response(200, json=stored)

    monkeypatch.setattr(
        "kptncook.mealie.download_image", lambda *a, **kw: b"invented-image"
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = MealieApiClient("https://example.test/api", client=http).create_recipe(
            RecipeWithImage(
                name="Invented",
                recipe_instructions=[
                    RecipeStep(
                        text="Mix",
                        image=Image(name="step.jpg", url="https://example.test/image"),
                    )
                ],
            )
        )
    assert result.name == "Invented"
    assert len(patches) == (1 if boundary == "patch" else 0)
    assert calls.count("POST") == 2  # Exactly one import and asset attempt.
    assert "DELETE" not in calls
    assert "private-fragment" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "Skipping step image" in caplog.text


def test_duplicate_conflict_detail_is_not_an_organizer_uniqueness_signal():
    from kptncook.http_errors import extract_mealie_detail_message

    response = httpx.Response(
        409,
        content='{"detail":{"message":"Other","message":"This item already exists."}}',
        headers={"content-type": "application/json"},
    )
    assert extract_mealie_detail_message(response) is None
