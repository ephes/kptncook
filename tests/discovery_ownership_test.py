"""Each workflow owns its new client through all processing stages."""

from types import SimpleNamespace

import httpx
import pytest

from kptncook.api import KptnCookClient
from kptncook.services import workflows


CASES = [
    ("get_today_recipes", {}, "list_today", []),
    ("list_dailies", {}, "list_dailies", []),
    ("get_kptncook_access_token", {}, "get_access_token", "synthetic"),
    ("get_discovery_screen", {}, "get_discovery_screen", {}),
    (
        "get_discovery_list_recipes",
        {"list_type": "latest", "list_id": None},
        "get_discovery_list",
        ["Abc1234"],
    ),
    ("list_popular_ingredients", {}, "list_popular_ingredients", []),
    (
        "get_recipes_with_ingredients",
        {"ingredient_ids": ["1"]},
        "get_recipes_with_ingredients",
        ["Abc1234"],
    ),
    (
        "get_onboarding_recipes",
        {"tags": ["vegan"]},
        "get_onboarding_recipes",
        ["Abc1234"],
    ),
    ("search_recipe_by_id", {"id_": "Abc1234"}, "get_by_ids", ["synthetic recipe"]),
]


@pytest.mark.parametrize("name,kwargs,method,payload", CASES)
@pytest.mark.parametrize("failure", [None, "request", "processing"])
def test_workflows_close_once(monkeypatch, name, kwargs, method, payload, failure):
    closes = []
    stages = []

    def fail_or_return(stage, result):
        assert not closes
        stages.append(stage)
        if failure == stage:
            if stage == "request":
                raise httpx.ReadError("synthetic failure")
            raise ValueError("synthetic processing failure")
        return result

    client = SimpleNamespace(close=lambda: closes.append(True))
    setattr(client, method, lambda *a, **kw: fail_or_return("request", payload))
    client.resolve_recipe_summaries = lambda *a: fail_or_return("processing", [])
    monkeypatch.setattr(workflows, "KptnCookClient", lambda: client)
    monkeypatch.setattr(workflows, "_require_access_token", lambda: None)
    monkeypatch.setattr(workflows, "get_credentials", lambda **kw: ("user", "password"))
    monkeypatch.setattr(
        workflows, "parse_discovery_screen", lambda p: fail_or_return("processing", p)
    )
    monkeypatch.setattr(
        workflows, "_save_repository_entries", lambda p: fail_or_return("processing", 1)
    )
    has_processing = name in {
        "get_discovery_screen",
        "get_discovery_list_recipes",
        "get_recipes_with_ingredients",
        "get_onboarding_recipes",
        "search_recipe_by_id",
    }
    if failure == "processing" and not has_processing:
        # Inject a decoding/validation error from the client itself.
        setattr(client, method, lambda *a, **kw: fail_or_return("processing", None))
    if failure:
        with pytest.raises((workflows.UserFacingError, httpx.ReadError, ValueError)):
            getattr(workflows, name)(**kwargs)
        assert failure in stages
    else:
        getattr(workflows, name)(**kwargs)
    assert closes == [True]


def test_summary_resolution_keeps_borrowed_client_open():
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    ) as http:
        client = KptnCookClient(client=http)
        assert workflows._resolve_recipe_summaries(client, [], action="synthetic") == []
        with client:
            pass
        assert not http.is_closed
        assert http.get("https://example.test/still-open").status_code == 200
