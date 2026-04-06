import pytest
import httpx
from pydantic_core import Url

from kptncook.api import KptnCookClient, RECIPE_RESOLUTION_TIMEOUT, looks_like_uid


def test_client_to_url():
    base_url = Url(
        "https://mobile.kptncook.com"
    )  # make sure urljoin works with pydantic URLs
    client = KptnCookClient(base_url=base_url)
    assert client.to_url("/recipes") == "https://mobile.kptncook.com/recipes"
    assert client.to_url("recipes") == "https://mobile.kptncook.com/recipes"


def test_client_reuses_injected_httpx_client():
    shared_client = httpx.Client()
    client = KptnCookClient(
        base_url="https://mobile.kptncook.com",
        api_key="test-key",
        client=shared_client,
    )

    assert client._client is shared_client

    shared_client.close()


def test_resolve_recipe_summaries_uses_bounded_timeout(monkeypatch):
    seen = {}
    client = KptnCookClient(base_url="https://mobile.kptncook.com", api_key="test-key")

    def fake_post(path, **kwargs):
        seen["path"] = path
        seen["timeout"] = kwargs.get("timeout")
        request = httpx.Request("POST", client.to_url(path))
        return httpx.Response(200, request=request, json=[])

    monkeypatch.setattr(client, "post", fake_post)

    recipes = client.resolve_recipe_summaries([("oid", "635a68635100007500061cd7")])

    assert recipes == []
    assert seen["path"] == "/recipes/search?kptnkey=test-key"
    assert seen["timeout"] == RECIPE_RESOLUTION_TIMEOUT


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
