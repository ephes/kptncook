import httpx
import pytest

import kptncook


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
    called = {"value": False}

    def fake_list_recipes():
        called["value"] = True

    monkeypatch.setattr(kptncook, "list_recipes", fake_list_recipes)

    kptncook.list_recipes_alias()

    assert called["value"] is True


def test_search_by_id_share_url_handles_http_error(monkeypatch):
    def fake_get(_url):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(kptncook.httpx, "get", fake_get)

    with pytest.raises(SystemExit):
        kptncook.search_kptncook_recipe_by_id(
            "https://share.kptncook.com/Dh4a/351k4802"
        )
