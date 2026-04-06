import json
from importlib import import_module

import httpx
import pytest
import typer

import kptncook as cli_mod
from kptncook.api import KptnCookClient, _collect_recipe_identifiers
from kptncook.services.discovery import normalize_discovery_list_type


def _json_response(url: str, payload: object) -> httpx.Response:
    request = httpx.Request("GET", url)
    content = json.dumps(payload).encode("utf-8")
    return httpx.Response(
        200,
        request=request,
        content=content,
        headers={"content-type": "application/json"},
    )


@pytest.mark.parametrize(
    ("list_type", "list_id", "expected_path"),
    [
        ("latest", None, "/discovery/list/latest"),
        ("recommended", None, "/discovery/list/recommended"),
        ("curated", "abc123", "/discovery/list/curated/abc123"),
        ("automated", "xyz987", "/discovery/list/automated/xyz987"),
    ],
)
def test_get_discovery_list_paths(monkeypatch, list_type, list_id, expected_path):
    captured = {}
    client = KptnCookClient(base_url="https://example.com", api_key="test-key")

    def fake_get(path, **kwargs):
        url = client.to_url(path)
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        return _json_response(url, {"recipes": []})

    monkeypatch.setattr(client, "get", fake_get)
    client.get_discovery_list(list_type=list_type, list_id=list_id)

    assert captured["url"] == f"https://example.com{expected_path}"
    assert captured["params"]["kptnkey"] == "test-key"


def test_get_discovery_list_requires_id():
    client = KptnCookClient(base_url="https://example.com", api_key="test-key")
    with pytest.raises(ValueError):
        client.get_discovery_list(list_type="curated")


def test_list_dailies_includes_filters(monkeypatch):
    captured = {}
    client = KptnCookClient(base_url="https://example.com", api_key="test-key")

    def fake_get(path, **kwargs):
        url = client.to_url(path)
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        return _json_response(url, [])

    monkeypatch.setattr(client, "get", fake_get)
    client.list_dailies(
        recipe_filter="veggie",
        zone="+02:00",
        is_subscribed=True,
        lang="fr",
        store="ch",
        preferences="rt:diet_vegetarian,",
    )

    params = captured["params"]
    assert captured["url"] == "https://example.com/dailies"
    assert params["kptnkey"] == "test-key"
    assert params["lang"] == "fr"
    assert params["store"] == "ch"
    assert params["preferences"] == "rt:diet_vegetarian,"
    assert params["recipeFilter"] == "veggie"
    assert params["zone"] == "+02:00"
    assert params["isSubscribed"] == "true"


def test_collect_recipe_identifiers_from_summaries():
    items = [
        {"id": "5aa2cbb028000052091b5c6c"},
        {"uid": "19e2eda2"},
        {"_id": {"$oid": "5aa2cbb028000052091b5c6d"}},
        "19e2eda2",
        {"identifier": {"$oid": "5aa2cbb028000052091b5c6e"}},
    ]

    assert _collect_recipe_identifiers(items) == [
        ("oid", "5aa2cbb028000052091b5c6c"),
        ("uid", "19e2eda2"),
        ("oid", "5aa2cbb028000052091b5c6d"),
        ("oid", "5aa2cbb028000052091b5c6e"),
    ]


def test_extract_quick_search_entries():
    payload = {"quickSearchEntries": [{"title": "Winter"}]}
    entries = cli_mod._extract_quick_search_entries(payload)
    assert entries == [{"title": "Winter"}]


def test_extract_ingredient_name_number_title():
    entry = {"numberTitle": {"singular": "Karotte", "plural": "Karotten"}}
    assert cli_mod._extract_ingredient_name(entry) == "Karotte"


def test_normalize_discovery_list_type_raises_value_error():
    with pytest.raises(ValueError):
        normalize_discovery_list_type("bogus")


def test_discovery_list_invalid_type_raises_bad_parameter():
    cli_module = import_module("kptncook.cli")

    with pytest.raises(typer.BadParameter):
        cli_module.list_discovery_list(list_type="bogus")


def test_dailies_http_error_message(monkeypatch, capsys):
    request = httpx.Request("GET", "https://example.com/dailies")
    response = httpx.Response(
        401,
        request=request,
        json={"message": "missing_kptnkey"},
        headers={"content-type": "application/json"},
    )

    def fake_list_dailies(*_args, **_kwargs):
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr(cli_mod.KptnCookClient, "list_dailies", fake_list_dailies)

    with pytest.raises(SystemExit):
        cli_mod.list_kptncook_dailies()

    out = capsys.readouterr().out
    assert "HTTP 401 while fetching dailies" in out
    assert "missing_kptnkey" in out
