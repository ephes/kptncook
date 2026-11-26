import json

import httpx
import pytest

from kptncook.api import KptnCookClient, parse_id
from kptncook.services import workflows


@pytest.mark.parametrize(
    "test_input,expected",
    [
        ("", None),
        ("asdf", None),
        (
            (
                "https://mobile.kptncook.com/recipe/pinterest/"
                "Rote-Pasta-mit-Salbei-Zitronen-Butter-&-karamell"
                "isierten-Waln%C3%BCssen/19e2eda2?_branch_match_id"
                "=866187386318966341&utm_source=SMS&utm_medium=sharing"
                "&_branch_referrer=H4sIAAAAAAAAA8soKSkottLXL85ILMrMS9fL"
                "LijJS87Pz9ZLzs%2FVN3b2SY4sTzatyk8CACm3OLEoAAAA"
            ),
            ("uid", "19e2eda2"),
        ),
        (
            (
                "https://mobile.kptncook.com/recipe/pinterest/"
                "Rote-Pasta-mit-Salbei-Zitronen-Butter-&-karamell"
                "isierten-Waln%C3%BCssen/19e2eda2?_branch_match_id"
                "=866187386318966341&utm_source=SMS&utm_medium=sharing"
                "&_branch_referrer=H4sIAAAAAAAAA8soKSkottLXL85ILMrMS9fL"
                "LijJS87Pz9ZLzs%2FVN3b2SY4sTzatyk8CACm3OLEoAAAA"
            ),
            ("uid", "19e2eda2"),
        ),
        ("19e2eda2", ("uid", "19e2eda2")),
        ("5aa2cbb028000052091b5c6c", ("oid", "5aa2cbb028000052091b5c6c")),
    ],
)
def test_parse_id(test_input, expected):
    print(test_input, expected)
    assert parse_id(test_input) == expected


@pytest.mark.parametrize("value", ["z" * 24, "ä" * 24, "Ａ" * 24, "0" * 23 + "g"])
def test_reject_nonhex_oid(value):
    assert parse_id(value) is None


@pytest.mark.parametrize("suffix", ["", "?lang=de", "#recipe", "?lang=de#recipe"])
def test_oid_url_boundaries(suffix):
    oid = "aB0123456789cDEF01234567"
    assert parse_id(f"https://share.kptncook.com/{oid}{suffix}") == ("oid", oid)


def test_pasted_oid_and_uid_precedence():
    oid = "a" * 24
    assert parse_id(f"Recipe: {oid}, next") == ("oid", oid)
    assert parse_id(f"{oid}/Ab12cd3") == ("uid", "Ab12cd3")


@pytest.mark.parametrize(
    "identifier",
    [{"uid": "Ab12Cd34"}, {"identifier": "aB0123456789cDEF01234567"}],
)
def test_search_sends_mobile_recipe_path_leaf(monkeypatch, identifier):
    leaf = next(iter(identifier.values()))

    def respond(request):
        assert request.url.path == "/recipes/search"
        assert json.loads(request.content) == [identifier]
        return httpx.Response(200, json=[{}])

    monkeypatch.setattr(workflows, "_repository_id_map", dict)
    monkeypatch.setattr(workflows, "_save_repository_entries", lambda *args, **kw: None)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = KptnCookClient("https://api.example", api_key="synthetic", client=http)
        monkeypatch.setattr(workflows, "_kptncook_client", lambda: client)
        result = workflows.search_recipe_by_id(
            f"https://mobile.kptncook.com/recipe/pinterest/Pancake/{leaf}"
            "?utm_source=sharing#Tracking"
        )

    assert result.id_value == leaf


@pytest.mark.parametrize(
    "url",
    [
        "https://mobile.kptncook.com:443/recipe/pinterest/Pancake/Ab12Cd34",
        "https://user@MOBILE.KptnCook.com/recipe/pinterest/Pancake/Ab12Cd34",
    ],
)
def test_recognized_host_with_port_or_userinfo_uses_path_leaf(url):
    assert parse_id(url) == ("uid", "Ab12Cd34")


def test_share_url_uses_path_leaf():
    assert parse_id("https://share.kptncook.com/Example/aBc1234?utm_medium=share") == (
        "uid",
        "aBc1234",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/recipe/pinterest/Pancake/Ab12Cd34",
        "https://mobile.kptncook.com/other/Pancake/Ab12Cd34",
    ],
)
def test_unrecognized_urls_keep_generic_precedence(url):
    assert parse_id(url) == ("uid", "Pancake")
