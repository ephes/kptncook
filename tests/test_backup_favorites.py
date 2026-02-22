import httpx
import pytest

import kptncook as cli_mod


def test_backup_favorites_redirect_error(monkeypatch, capsys):
    request = httpx.Request("GET", "https://mobile.kptncook.com/favorites")
    response = httpx.Response(301, request=request)

    def fake_list_favorites(*_args, **_kwargs):
        raise httpx.HTTPStatusError("redirect", request=request, response=response)

    monkeypatch.setattr(cli_mod.KptnCookClient, "list_favorites", fake_list_favorites)
    monkeypatch.setattr(cli_mod, "_require_access_token", lambda *a, **kw: None)

    with pytest.raises(SystemExit):
        cli_mod.backup_kptncook_favorites()

    out = capsys.readouterr().out
    assert "301" in out
    assert "no longer be available" in out


def test_backup_favorites_http_error(monkeypatch, capsys):
    request = httpx.Request("GET", "https://mobile.kptncook.com/favorites")
    response = httpx.Response(500, request=request)

    def fake_list_favorites(*_args, **_kwargs):
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(cli_mod.KptnCookClient, "list_favorites", fake_list_favorites)
    monkeypatch.setattr(cli_mod, "_require_access_token", lambda *a, **kw: None)

    with pytest.raises(SystemExit):
        cli_mod.backup_kptncook_favorites()

    out = capsys.readouterr().out
    assert "500" in out
    assert "no longer be available" not in out


def test_backup_favorites_transport_error(monkeypatch, capsys):
    request = httpx.Request("GET", "https://mobile.kptncook.com/favorites")

    def fake_list_favorites(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(cli_mod.KptnCookClient, "list_favorites", fake_list_favorites)
    monkeypatch.setattr(cli_mod, "_require_access_token", lambda *a, **kw: None)

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
    monkeypatch.setattr(cli_mod, "_require_access_token", lambda *a, **kw: None)
    monkeypatch.setattr(
        cli_mod, "_collect_recipe_identifiers", lambda items: [("apiId", "abc")]
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
