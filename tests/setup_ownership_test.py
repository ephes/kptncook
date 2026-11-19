"""Guided setup owns its auth pool and publishes only a complete token."""

from unittest.mock import Mock

import httpx
import pytest
from typer.testing import CliRunner

import kptncook.setup as setup


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "unauthorized",
        "http_error",
        "network",
        "invalid_json",
        "missing",
        "list",
        "null",
        "empty",
        "blank",
        "number",
        "write_error",
    ],
)
def test_setup_closes_owned_auth_client_and_preserves_previous_token(
    tmp_path, monkeypatch, outcome
):
    env = tmp_path / ".env"
    before = "KPTNCOOK_API_KEY=synthetic-key\nKPTNCOOK_ACCESS_TOKEN=previous-token\n"
    env.write_text(before)
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == "/auth/login"
        if outcome == "network":
            raise httpx.ConnectError("synthetic-private-auth-detail", request=request)
        if outcome in {"unauthorized", "http_error"}:
            return httpx.Response(
                401 if outcome == "unauthorized" else 500,
                json={"detail": "synthetic-private-auth-detail"},
            )
        if outcome == "invalid_json":
            return httpx.Response(200, content=b"synthetic-private-auth-detail")
        payload = {
            "missing": {"private": "synthetic-private-auth-detail"},
            "list": [],
            "null": {"accessToken": None},
            "empty": {"accessToken": ""},
            "blank": {"accessToken": "  "},
            "number": {"accessToken": 42},
        }.get(outcome, {"accessToken": "new-synthetic-token"})
        return httpx.Response(200, json=payload)

    http = httpx.Client(transport=httpx.MockTransport(respond))
    real_close = http.close
    close = Mock(wraps=real_close)
    monkeypatch.setattr(http, "close", close)
    # The wrapper creates this pool itself and therefore owns its lifetime.
    monkeypatch.setattr("kptncook.http_client.httpx.Client", lambda **kwargs: http)
    monkeypatch.setattr(
        setup, "get_credentials", lambda *a: ("synthetic-user", "synthetic-pw")
    )
    if outcome == "write_error":

        def fail_write(*args, **kwargs):
            close.assert_called_once()
            raise OSError("synthetic write refusal")

        monkeypatch.setattr(setup, "upsert_env_value", fail_write)
    try:
        result = CliRunner().invoke(setup.cli, ["--env-path", str(env)])
        close.assert_called_once()
        assert http.is_closed
        assert len(requests) == 1
        assert "synthetic-private-auth-detail" not in result.output
        if outcome == "success":
            assert result.exit_code == 0
            assert (
                setup.read_env_values(env)["KPTNCOOK_ACCESS_TOKEN"]
                == "new-synthetic-token"
            )
        else:
            assert result.exit_code == 1
            assert env.read_text() == before
            if outcome not in {"unauthorized", "http_error", "network", "write_error"}:
                assert "Invalid authentication response" in result.output
    finally:
        # Test cleanup must also close the pool on the unfixed implementation.
        if not http.is_closed:
            real_close()


@pytest.mark.parametrize("change_during", ["credentials", "login", "unrelated"])
def test_setup_binds_token_to_key_used_for_login(tmp_path, monkeypatch, change_during):
    env = tmp_path / ".env"
    env.write_text(
        "KPTNCOOK_API_KEY=synthetic-first\nKPTNCOOK_ACCESS_TOKEN=synthetic-old-token\n"
    )
    used_keys = []

    def credentials(*args):
        if change_during == "credentials":
            setup.upsert_env_value(env, "KPTNCOOK_API_KEY", "synthetic-next")
        return "synthetic-user", "synthetic-pw"

    def fetch(api_key, username, password):
        used_keys.append(api_key)
        if change_during == "login":
            setup.upsert_env_value(env, "KPTNCOOK_API_KEY", "synthetic-next")
        if change_during == "unrelated":
            setup.upsert_env_value(env, "KEEP", "synthetic-value")
        return "synthetic-new-token"

    monkeypatch.setattr(setup, "get_credentials", credentials)
    monkeypatch.setattr(setup, "_fetch_access_token", fetch)
    result = CliRunner().invoke(setup.cli, ["--env-path", str(env)])

    values = setup.read_env_values(env)
    assert used_keys == [
        "synthetic-next" if change_during == "credentials" else "synthetic-first"
    ]
    if change_during == "login":
        assert result.exit_code == 1
        assert "API key changed" in result.output
        assert "synthetic-" not in result.output
        assert values["KPTNCOOK_API_KEY"] == "synthetic-next"
        assert values["KPTNCOOK_ACCESS_TOKEN"] == "synthetic-old-token"
    else:
        assert result.exit_code == 0, result.output
        assert values["KPTNCOOK_ACCESS_TOKEN"] == "synthetic-new-token"
        if change_during == "unrelated":
            assert values["KEEP"] == "synthetic-value"
