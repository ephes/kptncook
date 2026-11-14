"""Opaque tokens are admitted before persistence or authenticated state changes."""

import httpx
import pytest
from typer.testing import CliRunner

from kptncook.api import KptnCookClient
from kptncook.mealie import MealieApiClient
from kptncook import setup

BAD_TOKENS = [
    None,
    42,
    [],
    {},
    "",
    " \t ",
    "a\rb",
    "a\nb",
    "a\x00b",
    "a\x1fb",
    "a\x7fb",
    "ümlaut",
    "x" * 8193,
]


@pytest.mark.parametrize(
    "token", BAD_TOKENS, ids=[f"bad-{i}" for i in range(len(BAD_TOKENS))]
)
def test_direct_mealie_token_preserves_auth_and_cache(token):
    with MealieApiClient("https://mealie.example/api") as client:
        client.login_with_token("previous-token")
        cache = {"tags": {"synthetic": []}}
        client._organizer_index = cache
        before = client.headers.copy()
        with pytest.raises(ValueError):
            client.login_with_token(token)
        assert client.headers == before
        assert client._organizer_index is cache


@pytest.mark.parametrize(
    "token", BAD_TOKENS, ids=[f"bad-{i}" for i in range(len(BAD_TOKENS))]
)
@pytest.mark.parametrize("kind", ["kptncook", "mealie", "setup"])
def test_login_token_refusal_before_save_or_state_change(
    tmp_path, monkeypatch, token, kind
):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"accessToken": token, "access_token": token})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        if kind == "setup":
            config = tmp_path / ".env"
            before = "KPTNCOOK_API_KEY=key\nKPTNCOOK_ACCESS_TOKEN=previous-token\n"
            config.write_text(before)
            client = KptnCookClient("https://api.example", api_key="key", client=http)
            monkeypatch.setattr(setup, "KptnCookClient", lambda **kw: client)
            monkeypatch.setattr(
                setup, "get_credentials", lambda *a: ("user", "password")
            )
            result = CliRunner().invoke(setup.cli, ["--env-path", str(config)])
            assert result.exit_code == 1
            assert "Invalid authentication" in result.output
            assert config.read_text() == before
        elif kind == "mealie":
            client = MealieApiClient("https://mealie.example/api", client=http)
            client.login_with_token("previous-token")
            cache = {"tags": {"synthetic": []}}
            client._organizer_index = cache
            before = client.headers.copy()
            with pytest.raises(ValueError):
                client.login("user", "password")
            assert client.headers == before
            assert client._organizer_index is cache
        else:
            client = KptnCookClient("https://api.example", api_key="key", client=http)
            with pytest.raises(ValueError):
                client.get_access_token("user", "password")
    assert len(requests) == 1


@pytest.mark.parametrize(
    "token", ["opaque.A-z_9~+/=:%!", "x" * 8192], ids=["opaque", "exact-limit"]
)
def test_valid_token_round_trips_unchanged(tmp_path, token):
    from kptncook.env import upsert_env_value, read_env_values

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"accessToken": token, "access_token": token}
            )
        )
    ) as http:
        client = KptnCookClient("https://api.example", api_key="key", client=http)
        assert client.get_access_token("user", "password") == token
        mealie = MealieApiClient("https://mealie.example/api", client=http)
        mealie.login("user", "password")
        assert mealie.headers["authorization"] == "Bearer " + token
        mealie.login_with_token(token)
        assert mealie.headers["authorization"] == "Bearer " + token
    config = tmp_path / ".env"
    upsert_env_value(config, "KPTNCOOK_ACCESS_TOKEN", token)
    assert read_env_values(config)["KPTNCOOK_ACCESS_TOKEN"] == token


@pytest.mark.parametrize(
    "token", BAD_TOKENS[1:], ids=[f"bad-{i}" for i in range(1, len(BAD_TOKENS))]
)
def test_kptncook_constructor_rejects_invalid_token(token):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("no request"))
    ) as http:
        with pytest.raises(ValueError):
            KptnCookClient(
                "https://api.example", api_key="key", access_token=token, client=http
            )


def test_blank_optional_config_token_stays_unset(tmp_path, monkeypatch):
    from kptncook.config import Settings

    config = tmp_path / ".env"
    config.write_text("KPTNCOOK_API_KEY=synthetic\nKPTNCOOK_ACCESS_TOKEN=\n")
    monkeypatch.setitem(Settings.model_config, "env_file", config)
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200))
    ) as http:
        client = KptnCookClient(client=http)
        assert not client.logged_in
        assert client.get("/today").status_code == 200
