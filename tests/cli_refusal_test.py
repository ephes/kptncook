"""Credential and envelope refusals end as short CLI errors, never tracebacks."""

import json
from importlib import import_module

import httpx
import pytest
from typer.testing import CliRunner

from kptncook.api import KptnCookClient
from kptncook.config import clear_settings_cache
from kptncook.http_errors import UserFacingError
from kptncook.services import workflows

runner = CliRunner()
cli_module = import_module("kptncook.cli")


def _response(payload: object) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://mobile.kptncook.com/x"),
    )


def _invalid_stored_token(monkeypatch) -> None:
    monkeypatch.setenv("KPTNCOOK_ACCESS_TOKEN", "synthetic bad token")
    clear_settings_cache()


def test_invalid_stored_token_is_a_short_cli_error(monkeypatch):
    _invalid_stored_token(monkeypatch)
    result = runner.invoke(cli_module.app, ["kptncook-today"])
    assert result.exit_code == 1
    assert "configured KPTNCOOK_ACCESS_TOKEN is invalid" in result.output
    assert "synthetic bad token" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_access_token_command_logs_in_despite_invalid_stored_token(monkeypatch):
    _invalid_stored_token(monkeypatch)
    monkeypatch.setattr(workflows, "get_credentials", lambda **kwargs: ("user", "pass"))
    seen = []

    def post(self, path, **kwargs):
        seen.append(self.headers.get("Token"))
        return _response({"accessToken": "fresh-token"})

    monkeypatch.setattr(KptnCookClient, "post", post)
    assert workflows.get_kptncook_access_token() == "fresh-token"
    assert seen == [None]


def test_refused_login_response_is_a_short_cli_error(monkeypatch, tmp_path):
    monkeypatch.setattr(workflows, "get_credentials", lambda **kwargs: ("user", "pass"))
    monkeypatch.setattr(
        KptnCookClient,
        "post",
        lambda self, path, **kwargs: _response({"accessToken": "a b"}),
    )
    saved = []
    monkeypatch.setattr(
        cli_module, "upsert_env_value", lambda *args: saved.append(args)
    )
    result = runner.invoke(cli_module.app, ["kptncook-access-token"])
    assert result.exit_code == 1
    assert "Login response did not contain a valid access token" in result.output
    assert "pass" not in result.output.replace("password", "")
    assert saved == []


def _deep(levels: int) -> dict:
    node: dict = {"leaf": True}
    for _ in range(levels):
        node = {"wrapper": node}
    return node


@pytest.mark.parametrize(
    "call",
    [
        lambda: workflows.get_discovery_list_recipes(list_type="latest", list_id=None),
        lambda: workflows.get_onboarding_recipes(["synthetic"]),
        lambda: workflows.get_recipes_with_ingredients(["synthetic"]),
    ],
    ids=["discovery", "onboarding", "ingredients"],
)
def test_refused_recipe_envelopes_are_user_facing(monkeypatch, call):
    monkeypatch.setenv("KPTNCOOK_ACCESS_TOKEN", "valid-token")
    clear_settings_cache()
    monkeypatch.setattr(
        KptnCookClient, "get", lambda self, path, **kw: _response(_deep(20))
    )
    monkeypatch.setattr(
        KptnCookClient, "post", lambda self, path, **kw: _response(_deep(20))
    )
    with pytest.raises(UserFacingError, match="cyclic or exceeds 16 levels"):
        call()


@pytest.mark.parametrize(
    "body", [b"{not json synthetic-detail", b"\x80\x81 synthetic-detail"]
)
def test_undecodable_recipe_list_is_user_facing_without_details(monkeypatch, body):
    bad = httpx.Response(
        200,
        content=body,
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://mobile.kptncook.com/x"),
    )
    monkeypatch.setattr(KptnCookClient, "get", lambda self, path, **kw: bad)
    with pytest.raises(UserFacingError) as caught:
        workflows.get_discovery_list_recipes(list_type="latest", list_id=None)
    assert str(caught.value) == "KptnCook returned an unreadable recipe list response."
