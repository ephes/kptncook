import pytest

from kptncook.api import parse_id


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
