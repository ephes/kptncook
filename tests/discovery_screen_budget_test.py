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
    assert (
        discovery.parse_discovery_screen(
            {"quickSearchEntries": [], "nested": payload}
        ).quick_search
        == []
    )


def test_screen_skips_blank_translations_in_each_fallback_tier():
    blank = {"de": "\t ", "en": "", "singular": "  ", "custom": "\n"}
    payload = {
        "lists": [
            {"title": {"de": " \t", "en": "  Fallback  ", "es": "Later"}},
            {"title": blank},
            {"id": "untitled", "title": blank},
        ],
        "quickSearch": [
            {"localizedTitle": {"de": "", "singular": " ", "plural": "Plural"}},
            {"localizedTitle": {"singular": "\n", "custom": " ", "other": "Other"}},
            {"localizedTitle": blank},
            {"localizedTitle": {"de": 42, "en": ["unsupported"]}},
        ],
    }

    result = discovery.parse_discovery_screen(payload)

    assert result.lists == [
        discovery.DiscoveryListSummary(None, "  Fallback  ", None),
        discovery.DiscoveryListSummary("untitled", None, None),
    ]
    assert result.quick_search == ["Plural", "Other"]


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


def test_screen_charges_probes_after_an_empty_alias(monkeypatch):
    payload = {"lists": [], "discoveryLists": [{"id": "list-soup"}]}
    keys = ("lists", "discoveryLists")
    monkeypatch.setattr(discovery, "MAX_SCREEN_WORK", 2)
    with pytest.raises(discovery.DiscoveryScreenError, match="work"):
        discovery._extract_nested_list(payload, keys)
    monkeypatch.setattr(discovery, "MAX_SCREEN_WORK", 3)
    assert discovery._extract_nested_list(payload, keys) == [{"id": "list-soup"}]


@pytest.mark.parametrize(
    "payload",
    [
        {"lists": [None] * 4097},
        {"quickSearch": ["x"] * 4097},
        {"lists": [{"id": "x"}] * 2048, "quickSearch": ["x"] * 2049},
    ],
)
def test_screen_leaf_entry_budget_precedes_summary_construction(monkeypatch, payload):
    built = []
    original = discovery.DiscoveryListSummary

    def summary(**kwargs):
        built.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(discovery, "DiscoveryListSummary", summary)
    closed = screen_service(monkeypatch, payload)
    with pytest.raises(workflows.UserFacingError, match="entry limit"):
        workflows.get_discovery_screen()
    assert built == []
    assert closed == [True]


@pytest.mark.parametrize("field", ["id", "title", "listType", "quickSearch"])
def test_screen_output_text_budget_precedes_summary_construction(monkeypatch, field):
    built = []
    monkeypatch.setattr(
        discovery, "DiscoveryListSummary", lambda **kw: built.append(kw)
    )
    payload = {"lists": [{"id": "a"}]}
    if field == "quickSearch":
        payload[field] = ["x" * 262144]
    else:
        payload["lists"].append({field: "x" * 262144})
    with pytest.raises(discovery.DiscoveryScreenError, match="text limit"):
        discovery.parse_discovery_screen(payload)
    assert built == []


def test_screen_leaf_and_text_exact_limits():
    payload = {"lists": [{"id": "x"}] * 2048, "quickSearch": ["x" * 127] * 2048}
    result = discovery.parse_discovery_screen(payload)
    assert len(result.lists) == len(result.quick_search) == 2048
    payload["quickSearch"][-1] += "x"
    with pytest.raises(discovery.DiscoveryScreenError, match="text limit"):
        discovery.parse_discovery_screen(payload)


@pytest.mark.parametrize("field", ["id", "title", "listType", "quickSearch"])
def test_screen_rejects_nonscalar_display_text_before_building_summaries(
    monkeypatch, field
):
    built = []
    monkeypatch.setattr(
        discovery, "DiscoveryListSummary", lambda **kw: built.append(kw)
    )
    payload = {"lists": [{"id": "early-valid-list"}]}
    if field == "quickSearch":
        payload[field] = ["private-label\udfff"]
    else:
        payload["lists"].append({field: "private-label\ud800"})
    closed = screen_service(monkeypatch, payload)

    with pytest.raises(workflows.UserFacingError) as error:
        workflows.get_discovery_screen()

    assert str(error.value) == "Malformed discovery screen: invalid Unicode scalar text"
    assert built == []
    assert closed == [True]


def test_screen_preserves_multilingual_and_supplementary_display_text(monkeypatch):
    payload = {
        "lists": [
            {
                "id": {"$oid": "献立𐐀"},
                "title": {"de": "Frühstück 🍳"},
                "listType": "curated",
            }
        ],
        "quickSearch": [{"localizedTitle": {"ja": "おにぎり 🍙"}}],
    }
    closed = screen_service(monkeypatch, payload)

    result = workflows.get_discovery_screen()

    assert result.lists == [
        discovery.DiscoveryListSummary("献立𐐀", "Frühstück 🍳", "curated")
    ]
    assert result.quick_search == ["おにぎり 🍙"]
    assert closed == [True]
