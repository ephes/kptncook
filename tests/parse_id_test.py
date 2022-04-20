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
