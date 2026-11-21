"""API credentials stay on the configured origin, including injected clients."""

import httpx
import pytest

from kptncook.api import KptnCookClient
from kptncook.http_client import BaseHttpClient
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


@pytest.mark.parametrize(
    "base_url,path,host,default_headers,client_headers,request_headers",
    [
        (
            "https://api.example/base",
            "https://API.EXAMPLE:443/items",
            "api.example",
            {"hOsT": "placeholder.example"},
            {},
            {},
        ),
        (
            "https://api.example:8443/base",
            "items",
            "api.example:8443",
            {},
            {"hOsT": "placeholder.example"},
            {},
        ),
        (
            "https://[::1]:8443/base",
            "items",
            "[::1]:8443",
            {},
            {"Host": "placeholder.example"},
            {"hOsT": "placeholder.example"},
        ),
    ],
)
def test_host_comes_from_admitted_url(
    base_url, path, host, default_headers, client_headers, request_headers
):
    def handle(request):
        assert request.headers.get_list("host") == [host]
        assert request.headers["authorization"] == "Bearer synthetic"
        assert request.url.params["q"] == "test"
        assert request.extensions["timeout"] == dict(
            connect=2.5, read=2.5, write=2.5, pool=2.5
        )
        return httpx.Response(200)

    with httpx.Client(
        transport=httpx.MockTransport(handle), headers=default_headers
    ) as http:
        client = BaseHttpClient(
            base_url,
            headers={"Authorization": "Bearer synthetic", **client_headers},
            client=http,
        )
        assert (
            client.get(
                path, headers=request_headers, params={"q": "test"}, timeout=2.5
            ).status_code
            == 200
        )


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
