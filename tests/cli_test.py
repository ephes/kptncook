from pathlib import Path
from importlib import import_module

import httpx
import pytest
from pydantic import ValidationError

import kptncook
from kptncook.config import Settings, SettingsError


def test_help_command_outputs_usage(capsys):
    kptncook.help_command(command=None, all_commands=False)

    output = capsys.readouterr().out
    assert "Usage:" in output


def test_help_command_all_outputs_usage(capsys):
    kptncook.help_command(command=None, all_commands=True)

    output = capsys.readouterr().out
    assert "Usage:" in output


def test_help_command_unknown_command_exits():
    with pytest.raises(SystemExit):
        kptncook.help_command(command="not-a-command", all_commands=False)


def test_ls_alias_calls_list_recipes(monkeypatch):
    cli_module = import_module("kptncook.cli")
    called = {"value": False}

    def fake_list_recipes():
        called["value"] = True

    monkeypatch.setattr(cli_module, "list_recipes", fake_list_recipes)

    kptncook.list_recipes_alias()

    assert called["value"] is True


def test_search_by_id_share_url_handles_http_error(monkeypatch):
    def fake_get(_url, **_kwargs):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(kptncook.httpx, "get", fake_get)

    with pytest.raises(SystemExit):
        kptncook.search_kptncook_recipe_by_id(
            "https://share.kptncook.com/Dh4a/351k4802"
        )


def test_command_renders_config_errors_at_cli_boundary(monkeypatch, capsys):
    cli_module = import_module("kptncook.cli")

    with pytest.raises(ValidationError) as exc_info:
        Settings(kptncook_api_key="   ")

    def fail_with_config_error():
        raise SettingsError(
            exc_info.value,
            missing_fields={"kptncook_api_key"},
            env_path=Path("/tmp/.kptncook/.env"),
            scaffolded=False,
        )

    monkeypatch.setattr(cli_module, "get_today_recipes", fail_with_config_error)

    with pytest.raises(SystemExit):
        cli_module.list_kptncook_today()

    output = capsys.readouterr().out
    assert "Missing required configuration." in output
    assert "KPTNCOOK_API_KEY=your-api-key-here" in output
