"""Guided setup owns its auth pool and publishes only a complete token."""

import os
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from typer.testing import CliRunner

import kptncook.setup as setup

COMMANDS_BEFORE = (
    "KPTNCOOK_API_KEY=synthetic-key\n"
    "KPTNCOOK_USERNAME_COMMAND=old-user\n"
    "KPTNCOOK_PASSWORD_COMMAND=old-password\n"
)


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


@pytest.mark.parametrize("competing_setup", [False, True])
def test_setup_initializes_api_key_only_if_still_blank(
    tmp_path, monkeypatch, competing_setup
):
    from kptncook import env as env_module

    path = tmp_path / ".env"
    path.write_text("KPTNCOOK_API_KEY='  '\nKEEP=synthetic\n")
    acquire = env_module._locked_env_path
    acquisitions = 0
    winner = None
    args = ["--env-path", str(path), "--no-fetch-access-token", "--api-key"]

    def interleave(target):
        nonlocal acquisitions, winner
        acquisitions += 1
        if acquisitions == 2 and competing_setup:
            # The first setup has inspected the blank file and is about to
            # acquire its update lock. Run the other setup to completion first.
            winner = CliRunner().invoke(setup.cli, [*args, "synthetic-winner"])
        return acquire(target)

    monkeypatch.setattr(env_module, "_locked_env_path", interleave)
    result = CliRunner().invoke(setup.cli, [*args, "synthetic-first"])

    assert result.exit_code == 0, result.output
    assert setup.read_env_values(path) == {
        "KPTNCOOK_API_KEY": "synthetic-winner"
        if competing_setup
        else "synthetic-first",
        "KEEP": "synthetic",
    }
    if competing_setup:
        assert winner is not None and winner.exit_code == 0
        assert "Added KPTNCOOK_API_KEY" in winner.output
        assert "KPTNCOOK_API_KEY is already set." in result.output
        assert "Added KPTNCOOK_API_KEY" not in result.output
    else:
        assert "Added KPTNCOOK_API_KEY" in result.output
    assert "synthetic-" not in result.output


def test_setup_keeps_both_commands_if_replacement_fails(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(COMMANDS_BEFORE)
    replace = os.replace
    credentials = Mock()
    monkeypatch.setattr(setup, "get_credentials", credentials)

    def fail_second_command(source, target):
        if target == path and "next-password" in Path(source).read_text():
            raise OSError("synthetic replacement refusal")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_second_command)
    result = CliRunner().invoke(
        setup.cli,
        [
            "--env-path",
            str(path),
            "--username-command",
            "next-user",
            "--password-command",
            "next-password",
        ],
    )

    assert result.exit_code == 1, result.output
    assert path.read_text() == COMMANDS_BEFORE
    assert "Could not read configuration or update it" in result.output
    assert "Stored KPTNCOOK_" not in result.output
    credentials.assert_not_called()


@pytest.mark.parametrize("paired_override", [False, True])
def test_setup_uses_its_committed_command_pair(tmp_path, monkeypatch, paired_override):
    path = tmp_path / ".env"
    path.write_text(COMMANDS_BEFORE)
    expected_password = "first-password" if paired_override else "old-password"
    args = ["--env-path", str(path)]
    credentials = Mock(return_value=("synthetic-user", "synthetic-password"))
    monkeypatch.setattr(setup, "get_credentials", credentials)
    monkeypatch.setattr(setup, "_fetch_access_token", lambda *args: "synthetic-token")
    print_message = setup.rprint
    observed = []
    second = None

    def command_pair():
        values = setup.read_env_values(path)
        return values["KPTNCOOK_USERNAME_COMMAND"], values["KPTNCOOK_PASSWORD_COMMAND"]

    def interleave(message):
        nonlocal second
        if message.startswith("Stored KPTNCOOK_USERNAME_COMMAND") and not observed:
            # Observe what a reader sees at the first store report, then run a
            # competing setup before the first one retrieves its credentials.
            observed.append(command_pair())
            second = CliRunner().invoke(
                setup.cli,
                [
                    *args,
                    "--username-command",
                    "second-user",
                    "--password-command",
                    "second-password",
                ],
            )
        print_message(message)

    monkeypatch.setattr(setup, "rprint", interleave)
    first_args = [*args, "--username-command", "first-user"]
    if paired_override:
        first_args.extend(["--password-command", "first-password"])
    first = CliRunner().invoke(setup.cli, first_args)

    assert first.exit_code == 0, first.output
    assert second is not None and second.exit_code == 0
    assert observed == [("first-user", expected_password)]
    assert command_pair() == ("second-user", "second-password")
    assert [call.args for call in credentials.call_args_list] == [
        ("second-user", "second-password"),
        ("first-user", expected_password),
    ]
