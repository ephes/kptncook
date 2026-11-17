"""CLI failures must not print the API key, access token or local variables."""

import importlib.util
from importlib import import_module

import httpx
import pytest
import typer
from typer.testing import CliRunner

from kptncook.api import KptnCookClient

SYNTHETIC_KEY = "synthetic-kptnkey-0123456789"
SYNTHETIC_TOKEN = "synthetic-access-token-0123456789"


def _failing_client(status_code: int | None) -> KptnCookClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if status_code is None:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(status_code, request=request, content=b"unavailable")

    return KptnCookClient(
        base_url="https://mobile.example.test",
        api_key=SYNTHETIC_KEY,
        access_token=SYNTHETIC_TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture
def failing_today(monkeypatch):
    workflows = import_module("kptncook.services.workflows")

    def install(status_code: int | None) -> None:
        monkeypatch.setattr(
            workflows, "_kptncook_client", lambda: _failing_client(status_code)
        )
        monkeypatch.setattr(workflows, "repository_needs_sync", lambda _day: True)
        monkeypatch.setattr(workflows, "_repository_id_map", lambda: {})

    return install


@pytest.mark.parametrize("command", ["kptncook-today", "save-todays-recipes", "sync"])
@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (500, "HTTP 500 while fetching today's recipes"),
        (403, "HTTP 403 while fetching today's recipes"),
        (None, "Request failed: connection error"),
    ],
)
def test_today_http_failures_are_value_free(
    failing_today, command, status_code, expected
):
    failing_today(status_code)
    cli_module = import_module("kptncook.cli")

    result = CliRunner().invoke(cli_module.app, [command])

    assert result.exit_code == 1
    assert expected in result.output
    assert SYNTHETIC_KEY not in result.output
    assert SYNTHETIC_TOKEN not in result.output
    # The failure is a handled CLI error, not an uncaught traceback.
    assert not isinstance(result.exception, httpx.HTTPError)


def test_unexpected_http_error_at_cli_boundary_is_value_free(monkeypatch, capsys):
    cli_module = import_module("kptncook.cli")
    request = httpx.Request(
        "GET", f"https://mobile.example.test/recipes?kptnkey={SYNTHETIC_KEY}"
    )
    response = httpx.Response(502, request=request)

    def unhandled() -> None:
        response.raise_for_status()

    with pytest.raises(SystemExit) as exc_info:
        cli_module._run_or_exit(unhandled)

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "HTTP 502" in out
    assert SYNTHETIC_KEY not in out


@pytest.mark.parametrize(
    ("module_name", "attribute"),
    [("kptncook.cli", "app"), ("kptncook.setup", "cli")],
)
def test_typer_apps_never_render_traceback_locals(monkeypatch, module_name, attribute):
    # Typer releases up to at least 0.16 render frame locals (credentials,
    # clients) by default, and the supported dependency range includes them.
    # Load a private copy of the module under such a default.
    class OldDefaultTyper(typer.Typer):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("pretty_exceptions_show_locals", True)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(typer, "Typer", OldDefaultTyper)
    source = import_module(module_name).__file__
    spec = importlib.util.spec_from_file_location(f"_probe_{attribute}", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert getattr(module, attribute).pretty_exceptions_show_locals is False
