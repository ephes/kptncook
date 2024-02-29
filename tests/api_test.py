from pydantic_core import Url

from kptncook.api import KptnCookClient


def test_client_to_url():
    base_url = Url(
        "https://mobile.kptncook.com"
    )  # make sure urljoin works with pydantic URLs
    client = KptnCookClient(base_url=base_url)
    assert client.to_url("/recipes") == "https://mobile.kptncook.com/recipes"
    assert client.to_url("recipes") == "https://mobile.kptncook.com/recipes"
