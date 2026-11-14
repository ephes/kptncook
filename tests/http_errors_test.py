"""Shared diagnostics and actual CLI catches never echo HTTP payloads."""

from importlib import import_module

import httpx
import pytest
from typer.testing import CliRunner

from kptncook.http_errors import (
    extract_http_error_message,
    format_http_status_error,
    format_request_error,
)
from kptncook.services import workflows

cli = import_module("kptncook.cli")
setup = import_module("kptncook.setup")


@pytest.mark.parametrize(
    "media",
    ["application/json", "APPLICATION/JSON; charset=UTF-8", "application/problem+json"],
)
@pytest.mark.parametrize(
    "payload",
    [
        {"message": "missing_kptnkey"},
        {"error": "missing_kptnkey"},
        {"detail": {"message": "missing_kptnkey"}},
    ],
)
def test_supported_json_error_projection(media, payload):
    response = httpx.Response(401, json=payload, headers={"Content-Type": media})
    assert extract_http_error_message(response) == "missing_kptnkey"
    assert (
        format_http_status_error(response, action="fetching recipes")
        == "HTTP 401 while fetching recipes: missing_kptnkey"
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "scalar",
        1,
        None,
        {"detail": []},
        {"detail": {"message": {"nested": "value"}}},
        {"message": "private token synthetic-secret"},
        {"message": "[red]injected[/red]"},
        {"message": "x" * 100_000},
    ],
)
def test_unknown_or_malformed_details_are_omitted(payload):
    response = httpx.Response(403, json=payload)
    assert extract_http_error_message(response) is None
    assert (
        format_http_status_error(response, action="fetching recipes")
        == "HTTP 403 while fetching recipes"
    )


@pytest.mark.parametrize(
    "content, media",
    [
        (b"{broken", "application/json"),
        (b"\xff", "application/problem+json"),
        (b'{"message":"missing_kptnkey"}', "text/html"),
        (b'{"message":"missing_kptnkey"}' + b" " * 100_000, "application/json"),
    ],
)
def test_invalid_or_oversized_json_details_are_omitted(content, media):
    response = httpx.Response(500, content=content, headers={"Content-Type": media})
    assert (
        format_http_status_error(response, action="fetching recipes")
        == "HTTP 500 while fetching recipes"
    )


@pytest.mark.parametrize(
    "exception, category",
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectError, "connection error"),
        (httpx.RemoteProtocolError, "protocol error"),
        (httpx.HTTPError, "HTTP error"),
    ],
)
def test_transport_diagnostic_is_a_static_category(exception, category):
    message = format_request_error(
        exception("Bearer synthetic-secret https://example.test/?signature=secret")
    )
    assert message == f"Request failed: {category}"


def test_redirect_hint_is_preserved():
    assert "endpoint may no longer be available" in format_http_status_error(
        httpx.Response(302), action="fetching favorites", unavailable_on_redirect=True
    )


@pytest.mark.parametrize(
    "command",
    [
        ["dailies"],
        ["discovery-screen"],
        ["search-by-id", "0123456789abcdef01234567"],
        ["kptncook-access-token"],
        ["setup"],
    ],
)
@pytest.mark.parametrize("failure", ["transport", "status"])
def test_cli_http_errors_never_expose_secrets(monkeypatch, tmp_path, command, failure):
    secrets = ["synthetic-key-77", "synthetic-bearer-77", "synthetic-signature-77"]
    monkeypatch.setenv("KPTNCOOK_API_KEY", secrets[0])
    monkeypatch.setenv("KPTNCOOK_ACCESS_TOKEN", secrets[1])
    request = httpx.Request(
        "GET",
        f"https://example.test/?signature={secrets[2]}",
        headers={"authorization": f"Bearer {secrets[1]}"},
    )
    response = httpx.Response(
        403,
        request=request,
        json={"message": " ".join(secrets) + " [bold]synthetic-payload-marker[/bold]"},
    )

    def fail(*args, **kwargs):
        if failure == "status":
            response.raise_for_status()
        raise httpx.ConnectError(" ".join(secrets) + str(request.url), request=request)

    for method in (
        "list_dailies",
        "get_discovery_screen",
        "get_by_ids",
        "get_access_token",
    ):
        monkeypatch.setattr(workflows.KptnCookClient, method, fail)
    monkeypatch.setattr(
        workflows,
        "get_credentials",
        lambda *a, **kw: ("synthetic-user", "synthetic-password"),
    )
    monkeypatch.setattr(
        setup,
        "get_credentials",
        lambda *a, **kw: ("synthetic-user", "synthetic-password"),
    )
    monkeypatch.setattr(setup, "_fetch_access_token", fail)
    args = command + (
        ["--env-path", str(tmp_path / "setup.env"), "--api-key", secrets[0]]
        if command == ["setup"]
        else []
    )
    result = CliRunner(env={"COLUMNS": "120"}).invoke(cli.app, args)
    assert result.exit_code == 1, result.output
    assert (
        "HTTP 403" in result.output
        if failure == "status"
        else "Request failed: connection error" in result.output
    )
    assert all(secret not in result.output for secret in secrets)
    assert "synthetic-payload-marker" not in result.output


def test_cli_escapes_rich_markup_at_error_boundary(monkeypatch):
    def fail():
        raise workflows.UserFacingError("HTTP 400 [bold]literal[/bold]")

    monkeypatch.setattr(cli, "get_discovery_screen", fail)
    result = CliRunner(env={"COLUMNS": "120"}).invoke(cli.app, ["discovery-screen"])
    assert result.exit_code == 1
    assert "[bold]literal[/bold]" in result.output


def test_share_url_transport_failure_is_private(monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ConnectError(
            "synthetic-secret-token https://example.test/?signature=private"
        )

    monkeypatch.setattr(workflows.httpx, "stream", fail)
    result = CliRunner(env={"COLUMNS": "120"}).invoke(
        cli.app, ["search-by-id", "https://share.kptncook.com/Abc1234"]
    )
    assert result.exit_code == 1
    assert "Request failed: connection error" in result.output
    assert "synthetic-secret-token" not in result.output
    assert "signature" not in result.output


def test_deep_json_detail_has_no_traceback():
    response = httpx.Response(
        500,
        content=b"[" * 2000 + b"0" + b"]" * 2000,
        headers={"Content-Type": "application/json"},
    )
    assert (
        format_http_status_error(response, action="fetching recipes")
        == "HTTP 500 while fetching recipes"
    )


def test_unread_response_is_not_consumed_for_diagnostics():
    response = httpx.Response(
        500,
        stream=httpx.ByteStream(b'{"message":"missing_kptnkey"}'),
        headers={"Content-Type": "application/json"},
    )
    try:
        assert (
            format_http_status_error(response, action="fetching recipes")
            == "HTTP 500 while fetching recipes"
        )
        assert not response.is_stream_consumed
    finally:
        response.close()


def test_json_decoder_recursion_failure_is_controlled(monkeypatch):
    response = httpx.Response(500, json={"detail": []})
    attempted = []

    def fail_decode(**kwargs):
        attempted.append(True)
        raise RecursionError("synthetic decoder failure")

    monkeypatch.setattr(response, "json", fail_decode)
    assert (
        format_http_status_error(response, action="fetching recipes")
        == "HTTP 500 while fetching recipes"
    )
    assert attempted == [True]


@pytest.mark.parametrize("failure", ["status", "transport", "userinfo"])
def test_mealie_login_error_is_private_through_sync_cli(monkeypatch, failure):
    secrets = ["synthetic-url-password", "synthetic-login-password", "synthetic-token"]
    monkeypatch.setenv(
        "MEALIE_URL",
        f"https://user:{secrets[0]}@example.test/api"
        if failure == "userinfo"
        else "https://example.test/api",
    )
    monkeypatch.setenv("MEALIE_USERNAME", "synthetic-user")
    monkeypatch.setenv("MEALIE_PASSWORD", secrets[1])
    monkeypatch.setenv("MEALIE_API_TOKEN", "")
    attempted = []

    def handle(request):
        attempted.append(request)
        if failure == "transport":
            raise httpx.ConnectError(
                " ".join(secrets) + str(request.url), request=request
            )
        return httpx.Response(403, json={"message": " ".join(secrets)})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr("kptncook.http_client.httpx.Client", lambda **kwargs: http)
    result = CliRunner(env={"COLUMNS": "120"}).invoke(cli.app, ["sync-with-mealie"])
    assert result.exit_code == 1
    assert bool(attempted) == (failure != "userinfo")
    assert http.is_closed
    assert all(secret not in result.output for secret in secrets)
    expected = {
        "status": "HTTP 403",
        "transport": "connection error",
        "userinfo": "origin",
    }
    assert expected[failure] in result.output
