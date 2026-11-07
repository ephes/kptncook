"""Acquisition budgets span successful pages and recipe detail reads."""

import httpx
import pytest

import kptncook.mealie as mealie
from kptncook.services import workflows
from kptncook.models import Recipe
from kptncook.services.repository import RepositoryRecipesResult


@pytest.mark.parametrize("slow", ["pages", "details", None])
def test_elapsed_inventory_budget_prevents_sync_writes(monkeypatch, minimal, slow):
    now = [0.0]
    monkeypatch.setattr(mealie, "monotonic", lambda: now[0], raising=False)
    monkeypatch.setattr(workflows, "monotonic", lambda: now[0], raising=False)
    writes = []
    rows = [
        {
            "slug": "existing",
            "extras": {
                "source": "kptncook",
                "kptncook_id": minimal["_id"]["$oid"]
                if slow is None
                else "different-synthetic-id",
            },
        },
        {"slug": "personal", "extras": {}},
    ]

    def handle(request):
        if request.method != "GET":
            writes.append(request)
            return httpx.Response(500)
        if request.url.path == "/api/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        listing = request.url.path == "/api/recipes"
        now[0] += (
            61
            if (listing and slow == "pages") or (not listing and slow == "details")
            else 1
        )
        if listing:
            page = int(request.url.params["page"])
            return httpx.Response(
                200, json={"items": [rows[page - 1]], "total_pages": 2}
            )
        return httpx.Response(
            200,
            json=next(
                row for row in rows if request.url.path.endswith("/" + row["slug"])
            ),
        )

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
        if slow:
            with pytest.raises(
                workflows.UserFacingError, match="inventory.*(deadline|time)"
            ):
                workflows.sync_with_mealie_result()
        else:
            assert workflows.sync_with_mealie_result().created_count == 0
    assert writes == []


@pytest.mark.parametrize("limit", ["pages", "items"])
def test_inventory_page_and_item_limits(monkeypatch, limit):
    monkeypatch.setattr(
        mealie, "MAX_INVENTORY_PAGES" if limit == "pages" else "MAX_INVENTORY_ITEMS", 2
    )
    pages = []

    def handle(request):
        page = int(request.url.params["page"])
        pages.append(page)
        if len(pages) > 3:
            pytest.fail("Unbounded inventory acquisition")
        return httpx.Response(
            200, json={"items": [{"name": f"tag-{page}"}], "total_pages": 3}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(mealie.MealieInventoryError, match="budget"):
            mealie.MealieApiClient(
                "https://mealie.example/api", client=http
            )._get_all_items("organizers/tags")
    assert pages == ([1] if limit == "pages" else [1, 2, 3])


def test_remaining_inventory_time_bounds_each_page_request(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(mealie, "monotonic", lambda: now[0], raising=False)
    timeouts = []

    def handle(request):
        timeouts.append(request.extensions["timeout"])
        now[0] += 2
        page = int(request.url.params["page"])
        return httpx.Response(
            200, json={"items": [{"name": f"tag-{page}"}], "total_pages": 3}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(mealie.MealieInventoryError, match="deadline"):
            mealie.MealieApiClient(
                "https://mealie.example/api", client=http, inventory_timeout=5
            )._get_all_items("foods")
    assert [entry["read"] for entry in timeouts] == [5, 3, 1]
    assert [entry["connect"] for entry in timeouts] == [5, 3, 1]


def test_remaining_budget_also_bounds_detail_requests(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(mealie, "monotonic", lambda: now[0])
    monkeypatch.setattr(workflows, "monotonic", lambda: now[0])
    timeouts = []

    def handle(request):
        timeouts.append(request.extensions["timeout"]["read"])
        if request.url.path == "/api/recipes":
            now[0] += 1
            return httpx.Response(
                200,
                json={"items": [{"slug": "one"}, {"slug": "two"}], "total_pages": 1},
            )
        now[0] += 2
        return httpx.Response(200, json={"slug": request.url.path.rsplit("/", 1)[-1]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = mealie.MealieApiClient(
            "https://mealie.example/api", client=http, inventory_timeout=5
        )
        with pytest.raises(workflows.UserFacingError, match="deadline"):
            workflows.get_kptncook_recipes_from_mealie(client)
    assert timeouts == [5, 4, 2]


def test_existing_recipe_with_invalid_presentation_is_never_recreated(
    monkeypatch, minimal
):
    # Preservation evidence for scout66's completeness slice, already repaired
    # by scout65's fail-closed inventory bundle.
    existing = {
        "id": "657d0eb6-b70d-4f50-9c5b-fb9551b2cbef",
        "slug": "existing",
        "extras": {"source": "kptncook", "kptncook_id": minimal["_id"]["$oid"]},
        "nutrition": "invalid-presentation",
    }
    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append(request)
            return httpx.Response(500)
        if request.url.path == "/api/recipes":
            return httpx.Response(
                200, json={"items": [{"slug": "native"}, existing], "total_pages": 1}
            )
        if request.url.path == "/api/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.url.path == "/api/recipes/native":
            return httpx.Response(200, json={"slug": "native"})
        return httpx.Response(200, json={"items": [], "total_pages": 1})

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
        with pytest.raises(workflows.UserFacingError, match="inventory.*position 2"):
            workflows.sync_with_mealie_result()
    assert writes == []


@pytest.mark.parametrize("budget", [120, 200])
def test_large_inventory_can_raise_the_finite_budget(monkeypatch, budget):
    from kptncook.config import settings

    now = [0.0]
    monkeypatch.setattr(mealie, "monotonic", lambda: now[0])
    monkeypatch.setattr(workflows, "monotonic", lambda: now[0])
    monkeypatch.setenv("MEALIE_INVENTORY_TIMEOUT_SECONDS", str(budget))
    monkeypatch.setattr(settings, "mealie_api_token", "synthetic")
    rows = [{"slug": f"personal-{i}"} for i in range(4)]
    details = []

    def handle(request):
        assert request.method == "GET"
        if request.url.path == "/api/recipes":
            now[0] += 1
            return httpx.Response(200, json={"items": rows, "total_pages": 1})
        details.append(request)
        now[0] += 40
        return httpx.Response(200, json={"slug": request.url.path.rsplit("/", 1)[-1]})

    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult([], []),
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        monkeypatch.setattr(
            workflows,
            "MealieApiClient",
            lambda base, **kwargs: mealie.MealieApiClient(base, client=http, **kwargs),
        )
        if budget == 120:
            with pytest.raises(workflows.UserFacingError, match="deadline"):
                workflows.sync_with_mealie_result()
        else:
            assert workflows.sync_with_mealie_result().created_count == 0
    assert len(details) == (3 if budget == 120 else 4)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 86401])
def test_inventory_budget_must_be_positive_finite_and_bounded(tmp_path, value):
    from pydantic import ValidationError
    from kptncook.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            root=tmp_path,
            kptncook_api_key="synthetic",
            mealie_inventory_timeout_seconds=value,
        )
    with pytest.raises(ValueError, match="inventory timeout"):
        mealie.MealieApiClient("https://mealie.example/api", inventory_timeout=value)


def test_inventory_budget_accepts_the_documented_maximum(tmp_path):
    from kptncook.config import Settings

    assert (
        Settings(
            _env_file=None,
            root=tmp_path,
            kptncook_api_key="synthetic",
            mealie_inventory_timeout_seconds=86400,
        ).mealie_inventory_timeout_seconds
        == 86400
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200))
    ) as http:
        assert (
            mealie.MealieApiClient(
                "https://mealie.example/api", client=http, inventory_timeout=86400
            ).inventory_timeout
            == 86400
        )
