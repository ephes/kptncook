"""Synthetic HTTP boundary regressions for Mealie replies."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from kptncook.mealie import MealieApiClient, RecipeTag


def test_organizer_pages_preserve_order():
    pages = []

    def handle(request):
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(
            200, json={"items": [{"name": f"tag-{page}"}], "total_pages": 3}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        assert client._get_all_items("organizers/tags") == [
            {"name": f"tag-{page}"} for page in (1, 2, 3)
        ]
    assert pages == [1, 2, 3]


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {"items": "BROKEN", "total_pages": 1},
        {"items": [], "total_pages": True},
        {"items": [], "total_pages": -1},
        {"items": [], "total_pages": 1.5},
        {"items": [42], "total_pages": 1},
    ],
)
def test_malformed_organizer_page_prevents_create(bad):
    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append(request)
        page = int(request.url.params.get("page", 1))
        return httpx.Response(
            200,
            json=(
                {"items": [{"name": "first"}], "total_pages": 2} if page == 1 else bad
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        with pytest.raises(ValueError, match="inventory"):
            client._create_item_name_to_item_lookup(
                "organizers/tags", RecipeTag, {RecipeTag(name="new")}
            )
    assert writes == []


@pytest.mark.parametrize(
    "reply",
    [
        {},
        {"access_token": None},
        {"access_token": []},
        {"access_token": {}},
        {"access_token": ""},
        {"access_token": "   "},
        [],
        None,
    ],
)
@pytest.mark.parametrize("previous", [None, "old-token"])
def test_invalid_login_preserves_authorization(reply, previous):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=json.dumps(reply))

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        if previous:
            client.login_with_token(previous)
        before = client.headers.copy()
        with pytest.raises(ValueError, match="login"):
            client.login("synthetic-user", "synthetic-password")
        assert client.headers == before
    assert len(requests) == 1
    assert requests[0].url.path == "/api/auth/token"


def test_login_sends_form_and_forwards_returned_token():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/auth/token"):
            return httpx.Response(200, json={"access_token": "synthetic-token"})
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        client.login("synthetic-user", "p &+word")
        client.get("/recipes")
    assert parse_qs(requests[0].content.decode()) == {
        "username": ["synthetic-user"],
        "password": ["p &+word"],
    }
    assert requests[0].headers["content-type"] == "application/x-www-form-urlencoded"
    assert requests[1].headers["authorization"] == "Bearer synthetic-token"


@pytest.mark.parametrize("operation", ["login", "inventory"])
def test_non_json_reply_is_bounded(operation):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text="<html>synthetic</html>")
        )
    ) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        with pytest.raises(ValueError, match="Invalid Mealie"):
            if operation == "login":
                client.login("user", "password")
            else:
                client._get_all_items("foods")


@pytest.mark.parametrize("items", [[], [{"name": "unexpected"}]])
def test_zero_pages_only_allows_empty_inventory(items):
    writes = []

    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": items, "total_pages": 0})
        writes.append(request)
        return httpx.Response(201, json={"name": "new"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        if items:
            with pytest.raises(ValueError, match="inventory"):
                client._create_item_name_to_item_lookup(
                    "foods", RecipeTag, {RecipeTag(name="new")}
                )
        else:
            assert client._get_all_items("foods") == []
            assert (
                client._create_item_name_to_item_lookup(
                    "foods", RecipeTag, {RecipeTag(name="new")}
                )["new"].name
                == "new"
            )
    assert len(writes) == (0 if items else 1)
