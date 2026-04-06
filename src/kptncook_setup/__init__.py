"""
Setup entrypoint for first-run configuration.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import typer
from rich import print as rprint

from kptncook.api import KptnCookClient
from kptncook.env import (
    DEFAULT_API_KEY,
    ENV_PATH,
    ENV_TEMPLATE,
    read_env_values,
    scaffold_env_file,
    upsert_env_value,
)
from kptncook.http_errors import extract_http_error_message, format_request_error
from kptncook.password_manager import get_credentials

DEFAULT_API_URL = "https://mobile.kptncook.com"
__all__ = ["ENV_PATH", "ENV_TEMPLATE", "cli", "setup"]

cli = typer.Typer()


def _fetch_access_token(api_key: str, username: str, password: str) -> str:
    client = KptnCookClient(
        base_url=DEFAULT_API_URL, api_key=api_key, access_token=None
    )
    return client.get_access_token(username, password)


@cli.command()
def setup(
    env_path: Path = typer.Option(
        ENV_PATH, "--env-path", help="Path to the .env file to configure."
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="KptnCook API key (defaults to the value documented in README.md).",
    ),
    username_command: str | None = typer.Option(
        None,
        "--username-command",
        help="Command to retrieve username from a password manager.",
    ),
    password_command: str | None = typer.Option(
        None,
        "--password-command",
        help="Command to retrieve password from a password manager.",
    ),
    fetch_access_token: bool = typer.Option(
        True,
        "--fetch-access-token/--no-fetch-access-token",
        help="Prompt for credentials and fetch an access token.",
    ),
) -> None:
    """
    Interactive setup for kptncook configuration.
    """
    scaffolded = scaffold_env_file(env_path)
    if scaffolded:
        rprint(f"Created {env_path} with a starter template.")

    values = read_env_values(env_path)
    existing_api_key = values.get("KPTNCOOK_API_KEY", "").strip()
    if not existing_api_key:
        chosen_key = api_key or DEFAULT_API_KEY
        upsert_env_value(env_path, "KPTNCOOK_API_KEY", chosen_key)
        rprint("Added KPTNCOOK_API_KEY to the .env file.")
    else:
        chosen_key = existing_api_key
        rprint("KPTNCOOK_API_KEY is already set.")

    if username_command:
        upsert_env_value(env_path, "KPTNCOOK_USERNAME_COMMAND", username_command)
        rprint("Stored KPTNCOOK_USERNAME_COMMAND in the .env file.")
    if password_command:
        upsert_env_value(env_path, "KPTNCOOK_PASSWORD_COMMAND", password_command)
        rprint("Stored KPTNCOOK_PASSWORD_COMMAND in the .env file.")

    if not fetch_access_token:
        rprint("Skipping access token fetch.")
        return

    values = read_env_values(env_path)
    username, password = get_credentials(
        username_command or values.get("KPTNCOOK_USERNAME_COMMAND"),
        password_command or values.get("KPTNCOOK_PASSWORD_COMMAND"),
    )

    if not username or not password:
        rprint("[red]Failed to get credentials.[/red]")
        sys.exit(1)

    try:
        access_token = _fetch_access_token(chosen_key, username, password)
        upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", access_token)
        rprint("[green]✓ Access token retrieved successfully.[/green]")
        rprint(f"Saved KPTNCOOK_ACCESS_TOKEN to {env_path}.")
    except httpx.HTTPStatusError as exc:
        detail = extract_http_error_message(exc.response)
        if exc.response.status_code == 401:
            message = (
                "Login failed (HTTP 401). Check your email/password and make sure "
                "KPTNCOOK_API_KEY is set to your real API key."
            )
        else:
            message = f"HTTP {exc.response.status_code} while getting access token"
            if detail:
                message = f"{message}: {detail}"
        rprint(f"[red]{message}[/red]")
        sys.exit(1)
    except httpx.HTTPError as exc:
        rprint(f"[red]{format_request_error(exc)}[/red]")
        sys.exit(1)
