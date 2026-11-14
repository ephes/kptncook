"""Envelope search budgets apply to direct library input and backup workflows."""

import httpx
import pytest

from kptncook.api import (
    _extract_discovery_list_payload,
    _extract_favorites_payload,
    KptnCookClient,
)
from kptncook.services import workflows


def malformed(kind):
    if kind == "cycle":
        value = {}
        value["wrapper"] = value
        return value
    if kind == "deep":
        value = {"items": ["synthetic"]}
        for _ in range(1500):
            value = {"wrapper": value}
        return value
    if kind == "wide":
        return {str(i): {"irrelevant": i} for i in range(5000)}
    return {str(i): i for i in range(5000)}


@pytest.mark.parametrize(
    "reader", [_extract_discovery_list_payload, _extract_favorites_payload]
)
@pytest.mark.parametrize("kind", ["cycle", "deep", "wide", "flat"])
def test_envelope_refuses_unbounded_search(reader, kind):
    with pytest.raises(ValueError, match="response wrapper"):
        reader(malformed(kind))


def test_envelope_precedence_and_empty_contract():
    assert _extract_discovery_list_payload({"items": [], "recipes": ["first"]}) == [
        "first"
    ]
    assert _extract_discovery_list_payload(
        {"a": {"items": []}, "b": {"items": ["second"]}}
    ) == ["second"]
    assert (
        _extract_discovery_list_payload({"items": [], "child": {"items": ["ignored"]}})
        == []
    )
    assert _extract_favorites_payload({"favorites": None, "items": ["ignored"]}) == (
        [],
        True,
        True,
    )
    assert _extract_favorites_payload({"data": {"items": []}}) == ([], True, False)
    assert _extract_favorites_payload({"wrapper": {"items": ["nested"]}}) == (
        ["nested"],
        True,
        False,
    )
    assert _extract_discovery_list_payload({"wrapper": {"data": ["nested"]}}) == [
        "nested"
    ]
    shared = {"unknown": "value"}
    assert _extract_favorites_payload(
        {"a": shared, "b": shared, "c": {"items": []}}
    ) == ([], True, False)


@pytest.mark.parametrize("kind", ["cycle", "deep", "wide"])
def test_wrapper_refusal_prevents_backup_and_resolution(monkeypatch, kind):
    payload = malformed(kind)
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200))
    ) as http:
        client = KptnCookClient("https://api.example", api_key="synthetic", client=http)

        class Reply:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        monkeypatch.setattr(client, "get", lambda *a, **kw: Reply())
        monkeypatch.setattr(workflows, "KptnCookClient", lambda: client)
        monkeypatch.setattr(workflows, "_require_access_token", lambda: None)
        monkeypatch.setattr(
            workflows,
            "_resolve_recipe_summaries",
            lambda *a, **kw: pytest.fail("No resolution"),
        )
        monkeypatch.setattr(
            workflows,
            "_save_repository_entries",
            lambda *a: pytest.fail("No backup writes"),
        )
        with pytest.raises(workflows.UserFacingError, match="response wrapper"):
            workflows.backup_kptncook_favorites()


@pytest.mark.parametrize(
    "reader", [_extract_discovery_list_payload, _extract_favorites_payload]
)
@pytest.mark.parametrize("extra", [0, 1])
def test_envelope_exact_depth_limit(reader, extra):
    payload = {"items": ["synthetic"]}
    for _ in range(15 + extra):
        payload = {"wrapper": payload}
    if extra:
        with pytest.raises(ValueError, match="response wrapper"):
            reader(payload)
    else:
        result = reader(payload)
        assert (result[0] if reader is _extract_favorites_payload else result) == [
            "synthetic"
        ]


@pytest.mark.parametrize("favorites", [True, False])
def test_envelope_search_work_is_incremental(monkeypatch, favorites):
    import kptncook.api as api

    visits = []

    class CountingDict(dict):
        def values(self):
            for value in super().values():
                visits.append(value)
                yield value

    monkeypatch.setattr(api, "MAX_RESPONSE_WRAPPER_WORK", 8)
    reader = (
        api._extract_favorites_payload
        if favorites
        else api._extract_discovery_list_payload
    )
    allowed = 2 if favorites else 3  # one root visit plus five/four key probes
    reader({str(i): i for i in range(allowed)})
    with pytest.raises(ValueError, match="work limit"):
        reader(CountingDict({str(i): i for i in range(5000)}))
    assert len(visits) == allowed + 1
