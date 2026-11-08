"""Inventory recipe details must describe the row they were requested for."""

import httpx
import pytest

import kptncook.mealie as mealie
from kptncook.models import Recipe
from kptncook.services import workflows
from kptncook.services.repository import RepositoryRecipesResult

ID_A = "11111111-1111-4111-8111-111111111111"
ID_B = "22222222-2222-4222-8222-222222222222"


def _row(slug, recipe_id, kptncook_id):
    return {
        "id": recipe_id,
        "slug": slug,
        "extras": {"source": "kptncook", "kptncook_id": kptncook_id},
    }


def _run_sync(monkeypatch, minimal, pages, details):
    """Sync one local recipe against a synthetic Mealie; return write requests."""
    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append(f"{request.method} {request.url.path}")
            return httpx.Response(500)
        if request.url.path == "/api/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.url.path == "/api/recipes":
            page = int(request.url.params["page"])
            return httpx.Response(
                200, json={"items": pages[page - 1], "total_pages": len(pages)}
            )
        slug = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=details[slug])

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        monkeypatch.setattr(
            workflows,
            "get_mealie_client",
            lambda: mealie.MealieApiClient("https://mealie.example/api", client=http),
        )
        monkeypatch.setattr(
            workflows,
            "load_kptncook_recipes_from_repository",
            lambda: RepositoryRecipesResult([Recipe.model_validate(minimal)], []),
        )
        try:
            result = workflows.sync_with_mealie_result()
        except workflows.UserFacingError as exc:
            return writes, exc
        return writes, result


@pytest.mark.parametrize(
    "case",
    ["detail-for-other-recipe", "reused-response", "other-stable-id", "missing-id"],
)
def test_mismatched_detail_stops_sync_before_writes(monkeypatch, minimal, case):
    local_id = minimal["_id"]["$oid"]
    row_a = _row("recipe-a", ID_A, local_id)  # the local recipe is already imported
    row_b = _row("recipe-b", ID_B, "other-kptncook-id")
    if case == "detail-for-other-recipe":
        details = {"recipe-a": row_b, "recipe-b": row_b}
    elif case == "reused-response":
        details = {"recipe-a": row_b | {"slug": "recipe-a"}, "recipe-b": row_b}
    elif case == "other-stable-id":
        details = {"recipe-a": row_a | {"id": ID_B}, "recipe-b": row_b}
    else:
        details = {"recipe-a": {k: v for k, v in row_a.items() if k != "id"}}
        details["recipe-b"] = row_b

    writes, outcome = _run_sync(monkeypatch, minimal, [[row_a], [row_b]], details)

    # Without the binding, recipe A's identity would vanish and A be re-created.
    assert writes == []
    assert isinstance(outcome, workflows.UserFacingError)
    assert "does not match the requested recipe at inventory position 1" in str(outcome)


def test_renamed_during_inventory_stops_sync(monkeypatch, minimal):
    row_a = _row("recipe-a", ID_A, minimal["_id"]["$oid"])
    details = {"recipe-a": row_a | {"slug": "recipe-a-renamed"}}

    writes, outcome = _run_sync(monkeypatch, minimal, [[row_a]], details)

    assert writes == []
    assert "run the sync again" in str(outcome)


def test_matching_details_still_reconcile_across_reordered_pages(monkeypatch, minimal):
    local_id = minimal["_id"]["$oid"]
    row_a = _row("recipe-a", ID_A, local_id)
    row_b = _row("recipe-b", ID_B, "other-kptncook-id")
    # Details may carry more fields than list rows; identity is what must match.
    details = {
        "recipe-a": row_a | {"name": "Detail A", "description": "changed"},
        "recipe-b": row_b | {"name": "Detail B"},
    }

    writes, outcome = _run_sync(monkeypatch, minimal, [[row_b], [row_a]], details)

    assert writes == []  # already imported: nothing to create
    assert outcome.created_count == 0
    assert outcome.failed_recipes == []


def test_rows_without_server_id_bind_by_slug(minimal):
    row = mealie.Recipe.model_validate({"slug": "recipe-a"})
    workflows._require_detail_matches_row(
        row, mealie.Recipe.model_validate({"slug": "recipe-a", "id": ID_A}), 1
    )
    with pytest.raises(mealie.MealieInventoryError):
        workflows._require_detail_matches_row(
            row, mealie.Recipe.model_validate({"slug": "recipe-b"}), 1
        )
