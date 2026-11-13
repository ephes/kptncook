"""Discovery routes and identifier wrappers fail before authenticated work."""

from importlib import import_module

import httpx
import pytest
from typer.testing import CliRunner

from kptncook.api import KptnCookClient, _collect_recipe_identifiers
from kptncook.services import workflows


@pytest.mark.parametrize(
    "list_type,list_id",
    [
        ("../accounts/me", None),
        ("latest?admin=1", None),
        ("unknown", None),
        *[
            ("curated", value)
            for value in (
                "../",
                "%2f",
                "%252f",
                "a/b",
                "a\\b",
                "?q=1",
                "#x",
                ".",
                "..",
                "a\x00b",
                "a\nb",
            )
        ],
    ],
)
def test_discovery_route_refusal_sends_no_request(list_type, list_id):
    requests = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: requests.append(r) or httpx.Response(200, json=[])
        )
    ) as http:
        with KptnCookClient(
            base_url="https://api.example/base",
            api_key="synthetic",
            access_token="private",
            client=http,
        ) as client:
            with pytest.raises(ValueError):
                client.get_discovery_list(list_type=list_type, list_id=list_id)
    assert requests == []


def test_discovery_unicode_component_uses_shared_client():
    requests = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: requests.append(r) or httpx.Response(200, json=[])
        )
    ) as http:
        client = KptnCookClient(
            base_url="https://api.example/base",
            api_key="synthetic",
            access_token="private",
            client=http,
        )
        assert (
            client.get_discovery_list(list_type="curated", list_id="küche-1_2.a~") == []
        )
    assert requests[0].url.path == "/base/discovery/list/curated/küche-1_2.a~"
    assert requests[0].url.params["kptnkey"] == "synthetic"
    assert requests[0].headers["Token"] == "private"
    assert b"%C3%BC" in requests[0].url.raw_path


@pytest.mark.parametrize("value", ["../", "%2f", "a/b", "?q=1", "#x"])
def test_discovery_cli_refuses_before_workflow(monkeypatch, value):
    cli = import_module("kptncook.cli")
    monkeypatch.setattr(
        cli,
        "get_discovery_list_recipes",
        lambda **kw: pytest.fail("No workflow on invalid input"),
    )
    result = CliRunner().invoke(
        cli.app, ["discovery-list", "--list-type", "curated", "--list-id", value]
    )
    assert result.exit_code == 2
    assert "list-id" in result.output


def malformed_wrapper(kind):
    if kind == "cycle":
        value = {}
        value["recipe"] = value
        return value
    value = {"uid": "Abc1234"}
    for _ in range(1500):
        value = {"recipe": value}
    return value


@pytest.mark.parametrize("kind", ["cycle", "deep"])
def test_identifier_budget_prevents_search_request(kind):
    requests = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: requests.append(r) or httpx.Response(200, json=[])
        )
    ) as http:
        client = KptnCookClient(
            base_url="https://api.example", api_key="synthetic", client=http
        )
        with pytest.raises(ValueError, match="Recipe identifier wrapper"):
            client.resolve_recipe_summaries(
                [{"uid": "Good123"}, malformed_wrapper(kind)]
            )
    assert not requests


@pytest.mark.parametrize("kind", ["cycle", "deep"])
def test_identifier_budget_prevents_partial_backup(monkeypatch, kind):
    calls = []
    with KptnCookClient(base_url="https://api.example", api_key="synthetic") as client:
        monkeypatch.setattr(
            client,
            "list_favorites",
            lambda: [{"uid": "Good123"}, malformed_wrapper(kind)],
        )
        monkeypatch.setattr(client, "post", lambda *a, **kw: pytest.fail("No search"))
        monkeypatch.setattr(client, "close", lambda: calls.append("close"))
        monkeypatch.setattr(workflows, "KptnCookClient", lambda: client)
        monkeypatch.setattr(workflows, "_require_access_token", lambda: None)
        monkeypatch.setattr(
            workflows,
            "_save_repository_entries",
            lambda rows: pytest.fail("No partial save"),
        )
        with pytest.raises(
            workflows.UserFacingError, match="Recipe identifier wrapper"
        ):
            workflows.backup_kptncook_favorites()
        assert calls == ["close"]
        client._client.close()


def test_supported_identifier_wrappers_and_shared_children():
    leaf = {"_id": {"$oid": "5e5390e2740000cdf1381c64"}}
    value = leaf
    for _ in range(8):
        value = {"recipeSummary": value}
    assert _collect_recipe_identifiers(
        [value, {"recipe": leaf}, {"uid": "Abc1234"}]
    ) == [
        ("oid", "5e5390e2740000cdf1381c64"),
        ("uid", "Abc1234"),
    ]
