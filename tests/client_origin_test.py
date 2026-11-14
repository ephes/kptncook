"""API credentials stay on the configured origin, including injected clients."""

import httpx
import pytest

from kptncook.api import KptnCookClient
from kptncook.mealie import MealieApiClient


@pytest.mark.parametrize("kind", ["kptncook", "mealie"])
@pytest.mark.parametrize(
    "target",
    [
        "https://other.example/path",
        "http://api.example/path",
        "https://api.example:444/path",
        "https://user:secret@api.example/path",
        "https://user@api.example/path",
        "//other.example/path",
    ],
)
def test_origin_refusal_sends_no_credentials(kind, target):
    requests = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: requests.append(r) or httpx.Response(200)
        )
    ) as http:
        if kind == "kptncook":
            client = KptnCookClient(
                "https://api.example/base",
                api_key="synthetic-key",
                access_token="synthetic-token",
                client=http,
            )
        else:
            client = MealieApiClient("https://api.example/base", client=http)
            client.login_with_token("synthetic-token")
        with pytest.raises(ValueError, match="origin") as exc:
            client.get(target)
        assert "secret" not in str(exc.value)
    assert requests == []


@pytest.mark.parametrize(
    "path", ["items", "/items", "https://API.EXAMPLE:443/absolute"]
)
def test_same_origin_keeps_auth(path):
    requests = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: requests.append(r) or httpx.Response(200)
        )
    ) as http:
        client = KptnCookClient(
            "https://api.example/base",
            api_key="key",
            access_token="synthetic-token",
            client=http,
        )
        client.get(path)
    assert requests[0].headers["Token"] == "synthetic-token"
    assert requests[0].url.host == "api.example"


@pytest.mark.parametrize("explicit", [False, True])
def test_api_redirect_cannot_forward_token(explicit):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/path"})

    with httpx.Client(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as http:
        client = KptnCookClient(
            "https://api.example",
            api_key="key",
            access_token="synthetic-token",
            client=http,
        )
        if explicit:
            with pytest.raises(ValueError, match="redirect"):
                client.get("/path", follow_redirects=True)
        else:
            assert client.get("/path").status_code == 302
    assert all(r.url.host == "api.example" for r in requests)
