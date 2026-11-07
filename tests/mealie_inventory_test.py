"""Incomplete inventories must never drive remote creation."""

import httpx
import pytest

from kptncook.mealie import MealieApiClient, RecipeTag
from kptncook.models import Recipe
from kptncook.services import workflows
from kptncook.services.repository import RepositoryRecipesResult


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "bad", "slug": "existing", "nutrition": "synthetic-secret"},
        {"slug": "existing", "nutrition": "synthetic-secret"},
        {"slug": "", "extras": {}},
        {"slug": "existing", "extras": {"source": "kptncook", "kptncook_id": None}},
    ],
)
def test_invalid_recipe_inventory_stops_sync(monkeypatch, minimal, bad):
    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append(request)
            return httpx.Response(500)
        if request.url.path == "/api/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.url.path == "/api/recipes":
            return httpx.Response(
                200, json={"items": [{"slug": "valid"}, bad], "total_pages": 1}
            )
        return httpx.Response(200, json={"slug": "valid", "extras": {}})

    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult([Recipe.model_validate(minimal)], []),
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        monkeypatch.setattr(workflows, "get_mealie_client", lambda: client)
        with pytest.raises(workflows.UserFacingError, match="inventory") as error:
            workflows.sync_with_mealie_result()
    assert "synthetic-secret" not in str(error.value)
    assert writes == []


@pytest.mark.parametrize(
    "scenario",
    [
        "repeat",
        "drift",
        "empty",
        "huge",
        "bool",
        "negative",
        "duplicate-id",
        "wrong-page",
        "truncated-total",
    ],
)
@pytest.mark.parametrize("endpoint", ["recipes", "organizers/tags"])
def test_incomplete_pagination_refuses_writes(monkeypatch, minimal, endpoint, scenario):
    requests = []
    writes = []

    def handle(request):
        requests.append(request)
        if len(requests) > 2:
            pytest.fail("Inventory exceeded the expected request budget")
        if request.method != "GET":
            writes.append(request)
            return httpx.Response(500)
        page = int(request.url.params.get("page", 1))
        item = {"name": f"name-{page}", "slug": f"slug-{page}"}
        total = 2
        if scenario == "huge":
            total = 1000000000
        if scenario == "bool":
            total = True
        if scenario == "negative":
            total = -1
        if page == 2:
            if scenario == "repeat":
                item = {"name": "name-1", "slug": "slug-1"}
            if scenario == "drift":
                total = 3
            if scenario == "duplicate-id":
                item["slug"] = "slug-1"
        items = [] if scenario == "empty" and page == 2 else [item]
        payload = {"items": items, "total_pages": total}
        if scenario == "wrong-page":
            payload["page"] = 1
        if scenario == "truncated-total":
            payload["total"] = 10
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        monkeypatch.setattr(workflows, "get_mealie_client", lambda: client)
        monkeypatch.setattr(
            workflows,
            "load_kptncook_recipes_from_repository",
            lambda: RepositoryRecipesResult([Recipe.model_validate(minimal)], []),
        )
        error_class = workflows.UserFacingError if endpoint == "recipes" else ValueError
        with pytest.raises(error_class, match="inventory"):
            if endpoint == "recipes":
                workflows.sync_with_mealie_result()
            else:
                client._create_item_name_to_item_lookup(
                    endpoint, RecipeTag, {RecipeTag(name="new")}
                )
    assert len(requests) <= 2
    assert writes == []


def test_complete_recipe_pages_keep_every_identity():
    pages = []

    def handle(request):
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "slug": f"recipe-{page}",
                        "extras": {"source": "kptncook", "kptncook_id": f"id-{page}"},
                    }
                ],
                "total_pages": 3,
                "total": 3,
                "page": page,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = MealieApiClient(
            "https://mealie.example/api", client=http
        ).get_all_recipes()
    assert [r.extras["kptncook_id"] for r in result] == ["id-1", "id-2", "id-3"]
    assert pages == [1, 2, 3]


def test_fractional_ratings_remain_in_complete_inventory(monkeypatch, minimal):
    rows = [
        {
            "slug": "imported",
            "rating": 4.5,
            "extras": {"source": "kptncook", "kptncook_id": minimal["_id"]["$oid"]},
        },
        {"slug": "personal", "rating": 3.5, "extras": {}},
    ]
    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append(request)
            return httpx.Response(500)
        if request.url.path == "/api/recipes":
            return httpx.Response(200, json={"items": rows, "total_pages": 1})
        return httpx.Response(
            200,
            json=next(
                row for row in rows if request.url.path.endswith("/" + row["slug"])
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        monkeypatch.setattr(workflows, "get_mealie_client", lambda: client)
        monkeypatch.setattr(
            workflows,
            "load_kptncook_recipes_from_repository",
            lambda: RepositoryRecipesResult([Recipe.model_validate(minimal)], []),
        )
        assert workflows.sync_with_mealie_result().created_count == 0
    assert writes == []


def test_invalid_detail_reports_inventory_position_and_never_creates(monkeypatch):
    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append(request)
            return httpx.Response(500)
        if request.url.path == "/api/recipes":
            return httpx.Response(
                200,
                json={
                    "items": [{"slug": "first"}, {"slug": "second"}],
                    "total_pages": 1,
                },
            )
        return httpx.Response(
            200,
            json={"slug": "first"}
            if request.url.path.endswith("/first")
            else {"nutrition": "synthetic-private-data"},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        monkeypatch.setattr(
            workflows,
            "get_mealie_client",
            lambda: MealieApiClient("https://mealie.example/api", client=http),
        )
        with pytest.raises(
            workflows.UserFacingError, match="detail at inventory position 2"
        ) as error:
            workflows.sync_with_mealie_result()
    assert "synthetic-private-data" not in str(error.value)
    assert writes == []
