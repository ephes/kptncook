"""Exercise the presentation service, separately from recipe-list envelopes."""

from types import SimpleNamespace

import pytest

from kptncook.services import discovery, workflows


def screen_service(monkeypatch, payload):
    closed = []
    client = SimpleNamespace(
        get_discovery_screen=lambda: payload, close=lambda: closed.append(True)
    )
    monkeypatch.setattr(workflows, "_kptncook_client", lambda: client)
    return closed


@pytest.mark.parametrize("shape", ["deep", "wide", "cycle", "partial"])
def test_screen_service_refuses_boundedly_and_closes(monkeypatch, shape):
    if shape == "wide":
        payload = {str(i): {} for i in range(5000)}
    elif shape == "cycle":
        payload = {}
        payload["self"] = payload
    else:
        payload = (
            {"lists": [{"id": "invented", "title": {"de": "Erfunden"}}]}
            if shape == "partial"
            else {}
        )
        for _ in range(2000):
            payload = {"wrapper": payload}
        if shape == "partial":
            payload["lists"] = [{"id": "early"}]
    closed = screen_service(monkeypatch, payload)
    with pytest.raises(
        workflows.UserFacingError, match="Malformed discovery screen"
    ) as error:
        workflows.get_discovery_screen()
    assert len(str(error.value)) < 120
    assert closed == [True]


def test_screen_preserves_list_precedence_localization_and_order(monkeypatch):
    payload = {
        "first": {"lists": []},
        "second": {
            "discoveryLists": [
                {"id": {"$oid": "one"}, "title": {"de": "Eins", "en": "One"}},
                {"id": "two", "localizedTitle": {"en": "Two"}},
            ]
        },
        "quickSearchEntries": ["First", {"localizedTitle": {"de": "Zweiter"}}],
        "quickSearch": ["Ignored"],
    }
    closed = screen_service(monkeypatch, payload)
    result = workflows.get_discovery_screen()
    assert [(item.list_id, item.title) for item in result.lists] == [
        ("one", "Eins"),
        ("two", "Two"),
    ]
    assert result.quick_search == ["First", "Zweiter"]
    assert closed == [True]
    assert (
        discovery.parse_discovery_screen({"lists": [], "nested": payload}).lists == []
    )


def test_screen_shared_acyclic_dictionary_is_allowed():
    shared = {"irrelevant": {}}
    assert discovery.parse_discovery_screen({"one": shared, "two": shared}).lists == []


def test_screen_depth_boundary():
    payload = {"lists": [{"id": "invented"}], "quickSearch": ["Invented"]}
    for _ in range(15):
        payload = {"wrapper": payload}
    assert discovery.parse_discovery_screen(payload).quick_search == ["Invented"]
    with pytest.raises(discovery.DiscoveryScreenError, match="depth"):
        discovery.parse_discovery_screen({"wrapper": payload})


def test_screen_work_boundary(monkeypatch):
    monkeypatch.setattr(discovery, "MAX_SCREEN_WORK", 2)
    assert discovery._extract_nested_list({"lists": []}, ("lists",)) == []
    monkeypatch.setattr(discovery, "MAX_SCREEN_WORK", 1)
    with pytest.raises(discovery.DiscoveryScreenError, match="work"):
        discovery._extract_nested_list({"lists": []}, ("lists",))
