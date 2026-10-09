"""Opt-in tests against an externally provisioned, disposable Mealie server."""

import base64
import copy
import os
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from kptncook.mealie import (
    IngredientReference,
    MealieApiClient,
    Nutrition,
    Recipe,
    RecipeFood,
    RecipeIngredient,
    RecipeStep,
    RecipeTag,
    RecipeUnit,
    RecipeWithImage,
)
from kptncook.models import Image
from kptncook.models import Recipe as KptnCookRecipe
from kptncook.services import workflows
from kptncook.services.repository import RepositoryRecipesResult


class RecordingTransport(httpx.BaseTransport):
    """Delegate to real HTTP, optionally losing one committed archive response."""

    def __init__(self) -> None:
        self.inner = httpx.HTTPTransport(retries=0)
        self.requests: list[tuple[str, str]] = []
        self.timeout_next_archive = False
        self.committed_archive_timeouts = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        response = self.inner.handle_request(request)
        is_archive = request.method == "POST" and request.url.path.endswith(
            ("/recipes/create-from-zip", "/recipes/create/zip")
        )
        if self.timeout_next_archive and is_archive and response.is_success:
            # Drain and close the actual response: the real server has committed
            # the import before we simulate its response being lost in transit.
            response.read()
            response.close()
            self.timeout_next_archive = False
            self.committed_archive_timeouts += 1
            raise httpx.ReadTimeout("Injected after committed archive", request=request)
        return response

    def close(self) -> None:
        self.inner.close()

    def writes(self) -> list[tuple[str, str]]:
        return [item for item in self.requests if item[0] != "GET"]


@pytest.fixture
def mealie_server(
    monkeypatch,
) -> Iterator[tuple[MealieApiClient, RecordingTransport, set[str]]]:
    url = os.environ.get("MEALIE_TEST_URL")
    token = os.environ.get("MEALIE_TEST_TOKEN")
    if not url or not token or os.environ.get("MEALIE_TEST_DISPOSABLE") != "1":
        pytest.skip(
            "Requires MEALIE_TEST_URL, MEALIE_TEST_TOKEN and MEALIE_TEST_DISPOSABLE=1"
        )

    test_ids: set[str] = set()
    transport = RecordingTransport()
    with httpx.Client(transport=transport, timeout=30, trust_env=False) as http_client:
        client = MealieApiClient(url, client=http_client)
        client.login_with_token(token)
        about = client.get("/app/about")
        about.raise_for_status()
        assert about.json()["version"], "Server must expose its Mealie version"
        # Inject only client construction; all inventory, creation and sync calls
        # still run the production implementation over real HTTP.
        monkeypatch.setattr(workflows, "get_mealie_client", lambda: client)
        try:
            yield client, transport, test_ids
        finally:
            # Use a separate healthy transport even after a lost write response.
            # Inventory is authoritative; no title/prefix/baseline-diff deletion.
            with httpx.Client(timeout=30, trust_env=False) as cleanup_http:
                cleanup = MealieApiClient(url, client=cleanup_http)
                cleanup.login_with_token(token)
                for summary in cleanup.get_all_recipes():
                    recipe = cleanup.get_via_slug(summary.slug)
                    if recipe.extras.get("kptncook_id") in test_ids:
                        assert recipe.id is not None
                        # v1 DELETE accepts only slugs, even though GET accepts
                        # UUIDs. Delete only recipes whose test identity we read.
                        cleanup.delete_via_slug(recipe.slug)
                remaining = [
                    cleanup.get_via_slug(summary.slug)
                    for summary in cleanup.get_all_recipes()
                ]
                assert not any(
                    recipe.extras.get("kptncook_id") in test_ids for recipe in remaining
                ), "Test recipe cleanup left persisted identities behind"


def _payload(test_ids: set[str], *, title: str | None = None) -> RecipeWithImage:
    oid = uuid.uuid4().hex[:24]
    test_ids.add(oid)
    return RecipeWithImage(
        name=title or f"KptnCook integration {oid}",
        recipe_instructions=[RecipeStep(text="Stir gently.")],
        extras={"source": "kptncook", "kptncook_id": oid},
    )


def _local_repository(
    monkeypatch, minimal: dict, payloads: list[RecipeWithImage]
) -> None:
    recipes = []
    by_id = {}
    for payload in payloads:
        data = copy.deepcopy(minimal)
        data["_id"]["$oid"] = payload.extras["kptncook_id"]
        data["localizedTitle"] = {"de": payload.name}
        recipe = KptnCookRecipe.model_validate(data)
        recipes.append(recipe)
        by_id[recipe.id.oid] = payload
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(recipes=recipes, invalid_entries=[]),
    )
    monkeypatch.setattr(
        workflows,
        "kptncook_to_mealie",
        lambda recipe: by_id[recipe.id.oid].model_copy(deep=True),
    )


def _persisted(client: MealieApiClient, test_ids: set[str]) -> list[Recipe]:
    return [
        recipe
        for recipe in workflows.get_kptncook_recipes_from_mealie(client)
        if recipe.extras.get("kptncook_id") in test_ids
    ]


def test_same_title_distinct_identities_sync_once(mealie_server, monkeypatch, minimal):
    client, transport, test_ids = mealie_server
    title = f"KptnCook same title {uuid.uuid4().hex}"
    payloads = [_payload(test_ids, title=title), _payload(test_ids, title=title)]
    _local_repository(monkeypatch, minimal, payloads)

    first = workflows.sync_with_mealie_result()
    assert first.created_count == 2
    assert first.failed_recipes == []
    persisted = _persisted(client, test_ids)
    assert len(persisted) == 2
    assert {recipe.extras["kptncook_id"] for recipe in persisted} == test_ids
    assert len({recipe.id for recipe in persisted}) == 2
    assert len({recipe.slug for recipe in persisted}) == 2
    assert {recipe.name for recipe in persisted} == {title, f"{title} (1)"}
    for recipe in persisted:
        assert {"source": "kptncook"}.items() <= recipe.extras.items()

    writes_before = transport.writes()
    second = workflows.sync_with_mealie_result()
    assert second.created_count == 0
    assert second.failed_recipes == []
    assert transport.writes() == writes_before
    assert {recipe.id for recipe in _persisted(client, test_ids)} == {
        recipe.id for recipe in persisted
    }


def test_lost_committed_archive_response_is_not_deleted_or_retried(
    mealie_server, monkeypatch, minimal
):
    client, transport, test_ids = mealie_server
    payload = _payload(test_ids)
    _local_repository(monkeypatch, minimal, [payload])
    transport.timeout_next_archive = True

    first = workflows.sync_with_mealie_result()
    assert transport.committed_archive_timeouts == 1
    assert first.created_count == 0
    assert len(first.failed_recipes) == 1
    assert first.failed_recipes[0].recipe_id == payload.extras["kptncook_id"]
    assert "ReadTimeout" in first.failed_recipes[0].reason
    assert not any(method == "DELETE" for method, _ in transport.requests)
    persisted = _persisted(client, test_ids)
    assert len(persisted) == 1
    assert payload.extras.items() <= persisted[0].extras.items()
    assert (
        sum(
            path.endswith(("/recipes/create-from-zip", "/recipes/create/zip"))
            for _, path in transport.writes()
        )
        == 1
    )

    writes_before = transport.writes()
    second = workflows.sync_with_mealie_result()
    assert second.created_count == 0
    assert second.failed_recipes == []
    assert transport.writes() == writes_before
    assert [recipe.id for recipe in _persisted(client, test_ids)] == [persisted[0].id]


def test_archive_roundtrip_preserves_recipe_content(mealie_server):
    client, _, test_ids = mealie_server
    payload = _payload(test_ids)
    reference = uuid.uuid4()
    payload.recipe_ingredient = [
        RecipeIngredient(
            referenceId=reference,
            quantity=2.5,
            unit=RecipeUnit(name="integration spoon"),
            food=RecipeFood(name="integration flour"),
            note="sifted",
            disable_amount=False,
        )
    ]
    payload.tags = [RecipeTag(name="kptncook integration")]
    payload.recipe_instructions = [
        RecipeStep(
            text="Mix flour, then bake for 15 minutes.",
            ingredientReferences=[IngredientReference(referenceId=reference)],
        )
    ]
    payload.nutrition = Nutrition(calories="123", proteinContent="4", fatContent="5")

    created = client.create_recipe(payload)
    assert isinstance(created, Recipe)
    persisted = client.get_via_slug(created.slug)
    assert payload.extras.items() <= persisted.extras.items()
    assert persisted.name == payload.name
    assert len(persisted.recipe_ingredient) == 1
    ingredient = persisted.recipe_ingredient[0]
    assert ingredient.quantity == 2.5
    assert ingredient.note == "sifted"
    assert ingredient.food is not None and ingredient.food.name == "integration flour"
    assert ingredient.unit is not None and ingredient.unit.name == "integration spoon"
    assert {tag.name for tag in persisted.tags} == {"kptncook integration"}
    assert (
        persisted.recipe_instructions[0].text == "Mix flour, then bake for 15 minutes."
    )
    assert persisted.recipe_instructions[0].ingredientReferences == [
        IngredientReference(referenceId=reference)
    ]
    assert persisted.nutrition.calories == "123"
    assert persisted.nutrition.proteinContent == "4"
    assert persisted.nutrition.fatContent == "5"


@pytest.fixture
def local_image_url() -> Iterator[str]:
    # A tiny PNG served locally: never download KptnCook/CloudFront media.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII="
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/step.png"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_step_media_patch_preserves_imported_content(mealie_server, local_image_url):
    client, transport, test_ids = mealie_server
    payload = _payload(test_ids)
    payload.recipe_instructions = [
        RecipeStep(
            text="Stir gently.",
            image=Image(name="integration-step.png", type="step", url=local_image_url),
        )
    ]

    created = client.create_recipe(payload)
    persisted = client.get_via_slug(created.slug)
    assert payload.extras.items() <= persisted.extras.items()
    assert len(persisted.assets) == 1
    asset = persisted.assets[0]
    assert asset.file_name is not None
    assert persisted.recipe_instructions[0].text.startswith("Stir gently.")
    assert (
        f"/api/media/recipes/{persisted.id}/assets/{asset.file_name}"
        in persisted.recipe_instructions[0].text
    )
    assert any(method == "PATCH" for method, _ in transport.requests)
    media = client.get(f"/media/recipes/{persisted.id}/assets/{asset.file_name}")
    media.raise_for_status()
    assert media.content.startswith(b"\x89PNG\r\n\x1a\n")
