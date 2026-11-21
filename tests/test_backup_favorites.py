from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from typer.testing import CliRunner

import kptncook as cli_mod
from kptncook.cli import app
from kptncook.repositories import RecipeInDb
from kptncook.services import workflows


@pytest.mark.parametrize("fetch_fails", [False, True])
def test_backup_favorites_preserves_outcome_when_close_fails(
    monkeypatch, minimal, caplog, fetch_fails
):
    recipe = RecipeInDb(date=workflows.date.today(), data=minimal)
    client = SimpleNamespace(
        list_favorites=Mock(return_value=[{"_id": minimal["_id"]}]),
        resolve_recipe_summaries=Mock(return_value=[recipe]),
        close=Mock(side_effect=httpx.CloseError("private-cleanup-detail")),
    )
    if fetch_fails:
        client.list_favorites.side_effect = httpx.ReadTimeout("private-fetch-detail")
    save = Mock(return_value=1)
    monkeypatch.setattr(workflows, "_require_access_token", lambda: None)
    monkeypatch.setattr(workflows, "KptnCookClient", lambda: client)
    monkeypatch.setattr(workflows, "save_recipe_entries", save)

    result = CliRunner().invoke(app, ["backup-favorites"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    client.close.assert_called_once_with()
    if fetch_fails:
        save.assert_not_called()
        assert "Request failed: timeout" in result.output
        assert "Added" not in result.output
    else:
        save.assert_called_once_with([recipe], expected={})
        assert "Found 1 favorites" in result.output
        assert "Added 1 recipes to local repository" in result.output
        assert "KptnCook client cleanup failed." in result.output
    assert "KptnCook client cleanup failed." in caplog.text
    assert "private-" not in result.output + caplog.text


def test_backup_favorites_redirect_error(monkeypatch, capsys):
    request = httpx.Request("GET", "https://mobile.kptncook.com/accounts/me/favorites")
    response = httpx.Response(301, request=request)

    def fake_list_favorites(*_args, **_kwargs):
        raise httpx.HTTPStatusError("redirect", request=request, response=response)

    monkeypatch.setattr(cli_mod.KptnCookClient, "list_favorites", fake_list_favorites)
    monkeypatch.setattr(workflows, "_require_access_token", lambda *a, **kw: None)

    with pytest.raises(SystemExit):
        cli_mod.backup_kptncook_favorites()

    out = capsys.readouterr().out
    assert "301" in out
    assert "no longer be available" in out


def test_backup_favorites_http_error(monkeypatch, capsys):
    request = httpx.Request("GET", "https://mobile.kptncook.com/accounts/me/favorites")
    response = httpx.Response(500, request=request)

    def fake_list_favorites(*_args, **_kwargs):
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(cli_mod.KptnCookClient, "list_favorites", fake_list_favorites)
    monkeypatch.setattr(workflows, "_require_access_token", lambda *a, **kw: None)

    with pytest.raises(SystemExit):
        cli_mod.backup_kptncook_favorites()

    out = capsys.readouterr().out
    assert "500" in out
    assert "no longer be available" not in out


def test_backup_favorites_transport_error(monkeypatch, capsys):
    request = httpx.Request("GET", "https://mobile.kptncook.com/accounts/me/favorites")

    def fake_list_favorites(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(cli_mod.KptnCookClient, "list_favorites", fake_list_favorites)
    monkeypatch.setattr(workflows, "_require_access_token", lambda *a, **kw: None)

    with pytest.raises(SystemExit):
        cli_mod.backup_kptncook_favorites()

    out = capsys.readouterr().out
    assert "Request failed" in out


def test_backup_favorites_resolve_http_error(monkeypatch, capsys):
    """get_by_ids (now _resolve_recipe_summaries) errors are also caught."""
    request = httpx.Request("GET", "https://mobile.kptncook.com/recipes")
    response = httpx.Response(502, request=request)

    monkeypatch.setattr(
        cli_mod.KptnCookClient, "list_favorites", lambda *a, **kw: [{"id": "abc"}]
    )
    monkeypatch.setattr(workflows, "_require_access_token", lambda *a, **kw: None)
    monkeypatch.setattr(
        workflows, "_collect_recipe_identifiers", lambda items: [("apiId", "abc")]
    )

    def fake_resolve(*_args, **_kwargs):
        raise httpx.HTTPStatusError("bad gateway", request=request, response=response)

    monkeypatch.setattr(
        cli_mod.KptnCookClient, "resolve_recipe_summaries", fake_resolve
    )

    with pytest.raises(SystemExit):
        cli_mod.backup_kptncook_favorites()

    out = capsys.readouterr().out
    assert "502" in out
    assert "resolving recipes" in out
