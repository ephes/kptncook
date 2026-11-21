"""Public HTTP request and workflow ownership contracts."""

import httpx
import pytest

from kptncook.api import KptnCookClient
from kptncook.http_client import BaseHttpClient, DEFAULT_REQUEST_TIMEOUT
from kptncook.mealie import MealieApiClient
from kptncook.services import workflows
from kptncook.config import settings
from kptncook.models import Recipe
from kptncook.repositories import RecipeInDb
from kptncook.services.repository import RepositoryRecipesResult


def test_request_wire_contract():
    requests = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: requests.append(r) or httpx.Response(200)
        ),
        timeout=7,
    ) as http:
        client = BaseHttpClient(
            "https://api.example/base/",
            headers={"authorization": "Bearer synthetic", "X-Override": "base"},
            client=http,
        )
        client.get("items", headers={"X-Override": "request"}, params={"q": "test"})
        client.post("/items", timeout=2.5, json={"value": 1})
        client.get("https://api.example/absolute", timeout=httpx.Timeout(9, connect=3))
    assert str(requests[0].url) == "https://api.example/base/items?q=test"
    assert requests[0].headers["authorization"] == "Bearer synthetic"
    assert requests[0].headers["x-override"] == "request"
    assert requests[0].extensions["timeout"] == dict(connect=7, read=7, write=7, pool=7)
    assert requests[1].method == "POST"
    assert requests[1].url.path == "/base/items"
    assert requests[1].headers["x-override"] == "base"
    assert requests[1].extensions["timeout"] == dict(
        connect=2.5, read=2.5, write=2.5, pool=2.5
    )
    assert str(requests[2].url) == "https://api.example/absolute"
    assert requests[2].extensions["timeout"] == dict(connect=3, read=9, write=9, pool=9)


def test_explicit_wrapper_timeout_overrides_borrowed_client_default():
    requests = []
    transport = httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200))
    with httpx.Client(transport=transport, timeout=None) as http:
        inherited = BaseHttpClient("https://api.example", client=http)
        explicit = BaseHttpClient(
            "https://api.example", client=http, timeout=DEFAULT_REQUEST_TIMEOUT
        )
        inherited.get("/inherited")
        explicit.get("/explicit")
        explicit.get("/per-request", timeout=2.5)
        explicit.get("/unbounded", timeout=None)
        inherited.get("/still-inherited")

    unbounded = dict(connect=None, read=None, write=None, pool=None)
    assert [request.extensions["timeout"] for request in requests] == [
        unbounded,
        dict(connect=10, read=30, write=30, pool=30),
        dict(connect=2.5, read=2.5, write=2.5, pool=2.5),
        unbounded,
        unbounded,
    ]


def test_api_clients_inherit_timeout_of_borrowed_pool():
    requests = []
    transport = httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200))
    with httpx.Client(transport=transport, timeout=7) as http:
        KptnCookClient("https://api.example", "synthetic-key", client=http).get(
            "/items"
        )
        MealieApiClient("https://api.example", client=http).get("/items")

    assert [request.extensions["timeout"] for request in requests] == [
        dict(connect=7, read=7, write=7, pool=7),
    ] * 2


@pytest.mark.parametrize("owned", [True, False])
@pytest.mark.parametrize("exceptional", [True, False])
def test_context_closes_only_owned_http_client(monkeypatch, owned, exceptional):
    real_client = httpx.Client
    http = real_client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)), timeout=4
    )
    if owned:
        monkeypatch.setattr("kptncook.http_client.httpx.Client", lambda **kw: http)
    client = BaseHttpClient("https://api.example", client=None if owned else http)
    try:
        try:
            with client:
                assert client.get("/items").status_code == 200
                if exceptional:
                    raise RuntimeError("synthetic")
        except RuntimeError:
            assert exceptional
        assert http.is_closed == owned
        if not owned:
            assert http.get("https://api.example/still-usable").status_code == 200
    finally:
        http.close()


@pytest.mark.parametrize("request_fails", [True, False])
def test_owned_http_close_failure_preserves_primary_error(
    monkeypatch, caplog, request_fails
):
    request_error = httpx.ReadError("synthetic request failure")
    close_error = RuntimeError("synthetic private cleanup detail")

    class FailingTransport(httpx.MockTransport):
        def close(self):
            raise close_error

    def handle(request):
        if request_fails:
            raise request_error
        return httpx.Response(200)

    http = httpx.Client(transport=FailingTransport(handle))
    monkeypatch.setattr("kptncook.http_client.httpx.Client", lambda **kw: http)
    expected = request_error if request_fails else close_error
    with pytest.raises(type(expected)) as caught:
        with BaseHttpClient("https://api.example") as client:
            client.get("/items")

    assert caught.value is expected
    assert http.is_closed
    assert caplog.messages == (["HTTP client cleanup failed."] if request_fails else [])


class OwnedClient:
    def __init__(self, *args):
        self.closes = 0

    def close(self):
        self.closes += 1

    def login(self, *args):
        raise ValueError("synthetic login failure")

    def login_with_token(self, token):
        pass


@pytest.mark.parametrize("mode", ["missing", "failed", "healthy"])
def test_factory_ownership(monkeypatch, mode):
    client = OwnedClient()
    monkeypatch.setattr(workflows, "MealieApiClient", lambda *a, **kw: client)
    monkeypatch.setattr(
        settings, "mealie_api_token", "synthetic" if mode == "healthy" else None
    )
    monkeypatch.setattr(
        settings, "mealie_username", "user" if mode == "failed" else None
    )
    monkeypatch.setattr(
        settings, "mealie_password", "password" if mode == "failed" else None
    )
    if mode == "healthy":
        assert workflows.get_mealie_client() is client
        assert client.closes == 0
        client.close()
    else:
        with pytest.raises(workflows.UserFacingError):
            workflows.get_mealie_client()
    assert client.closes == 1


@pytest.mark.parametrize("failure", ["inventory", "repository", "recipe", None])
def test_sync_always_closes_owned_client(monkeypatch, minimal, failure):
    client = OwnedClient()

    def inventory(_):
        if failure == "inventory":
            raise RuntimeError("inventory")
        return []

    def repository():
        if failure == "repository":
            raise RuntimeError("repository")
        return RepositoryRecipesResult([Recipe.model_validate(minimal)], [])

    def create(recipe):
        if failure == "recipe":
            raise RuntimeError("recipe")

    client.create_recipe = create
    monkeypatch.setattr(workflows, "get_mealie_client", lambda: client)
    monkeypatch.setattr(workflows, "get_kptncook_recipes_from_mealie", inventory)
    monkeypatch.setattr(workflows, "load_kptncook_recipes_from_repository", repository)
    if failure in ("inventory", "repository"):
        with pytest.raises(RuntimeError, match=failure):
            workflows.sync_with_mealie_result()
    else:
        result = workflows.sync_with_mealie_result()
        assert result.created_count == (0 if failure else 1)
        assert len(result.failed_recipes) == (1 if failure else 0)
    assert client.closes == 1


@pytest.mark.parametrize("failure", ["fetch", "resolve", "save", "empty", None])
def test_favorites_always_closes_owned_client(monkeypatch, minimal, failure):
    client = OwnedClient()

    def fetch():
        if failure == "fetch":
            raise ValueError("fetch")
        return (
            []
            if failure == "empty"
            else [{"_id": {"$oid": "5e5390e2740000cdf1381c64"}}]
        )

    def resolve(*args, **kwargs):
        if failure == "resolve":
            raise RuntimeError("resolve")
        return [RecipeInDb(date="2026-11-07", data=minimal)]

    def save(recipes, *, expected):
        if failure == "save":
            raise RuntimeError("save")
        return 1

    client.list_favorites = fetch
    monkeypatch.setattr(workflows, "_require_access_token", lambda: None)
    monkeypatch.setattr(workflows, "KptnCookClient", lambda: client)
    monkeypatch.setattr(workflows, "_resolve_recipe_summaries", resolve)
    monkeypatch.setattr(workflows, "_save_repository_entries", save)
    if failure:
        with pytest.raises(
            (workflows.UserFacingError, RuntimeError),
            match=("favorites" if failure == "empty" else failure),
        ):
            workflows.backup_kptncook_favorites()
    else:
        assert workflows.backup_kptncook_favorites().saved_count == 1
    assert client.closes == 1
