import pytest
from pydantic_core import Url

from kptncook.api import KptnCookClient, looks_like_uid


def test_client_to_url():
    base_url = Url(
        "https://mobile.kptncook.com"
    )  # make sure urljoin works with pydantic URLs
    client = KptnCookClient(base_url=base_url)
    assert client.to_url("/recipes") == "https://mobile.kptncook.com/recipes"
    assert client.to_url("recipes") == "https://mobile.kptncook.com/recipes"


@pytest.mark.parametrize(
    "uid, expected_valid",
    [
        ("", False),  # too short
        ("1234567", True),  # 7 digits
        ("12345678", True),  # 8 digits
        ("123456789", False),  # too long
        ("#345d+&", False),  # invalid characters
    ],
)
def test_looks_like_uid(uid, expected_valid):
    assert looks_like_uid(uid) == expected_valid
