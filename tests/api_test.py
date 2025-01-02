from pydantic_core import Url

from kptncook.api import KptnCookClient


def test_client_to_url(test_settings):
    base_url = Url(
        "https://mobile.kptncook.com"
    )  # make sure urljoin works with pydantic URLs
    client = KptnCookClient(
        base_url=base_url,
        api_key=test_settings.kptncook_api_key,
        access_token=test_settings.kptncook_access_token,
    )
    assert client.to_url("/recipes") == "https://mobile.kptncook.com/recipes"
    assert client.to_url("recipes") == "https://mobile.kptncook.com/recipes"
