"""
Setup entrypoint for first-run configuration without importing kptncook.
"""

# NOTE: This module intentionally duplicates env helpers and credential prompts
# from the main package to avoid importing kptncook.__init__, which triggers
# settings validation. This keeps setup runnable before any config exists.

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from urllib.parse import urljoin

import httpx
import typer
from rich import print as rprint
from rich.prompt import Prompt

DEFAULT_API_KEY = "6q7QNKy-oIgk-IMuWisJ-jfN7s6"
DEFAULT_API_URL = "https://mobile.kptncook.com"
ENV_PATH = Path.home() / ".kptncook" / ".env"
ENV_TEMPLATE = f"""# kptncook configuration
#
# Required
KPTNCOOK_API_KEY={DEFAULT_API_KEY}
#
# Optional: access token for favorites
KPTNCOOK_ACCESS_TOKEN=
#
# Optional: Mealie sync
# MEALIE_URL=https://mealie.example.com/api
# MEALIE_API_TOKEN=
# MEALIE_USERNAME=
# MEALIE_PASSWORD=
#
# Optional: API defaults
# KPTNCOOK_LANG=de
# KPTNCOOK_STORE=de
# KPTNCOOK_PREFERENCES=rt:diet_vegetarian,
#
# Optional: password manager integration
# KPTNCOOK_USERNAME_COMMAND="op read op://Personal/KptnCook/username"
# KPTNCOOK_PASSWORD_COMMAND="op read op://Personal/KptnCook/password"
#
# Optional: ingredient grouping
# KPTNCOOK_GROUP_INGREDIENTS_BY_TYP=true
# KPTNCOOK_INGREDIENT_GROUP_LABELS="regular:You need,basic:Pantry"
"""

DEFAULT_HEADERS = {
    "content-type": "application/json",
    "Accept": "application/vnd.kptncook.mobile-v8+json",
    "User-Agent": "Platform/Android/12.0.1 App/7.10.1",
    "hasIngredients": "yes",
}

cli = typer.Typer()


def _scaffold_env_file(env_path: Path) -> bool:
    try:
        if env_path.exists() and env_path.stat().st_size > 0:
            return False
    except OSError:
        return False
    env_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        env_path.write_text(ENV_TEMPLATE)
    except OSError:
        return False
    return True


def _read_env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        content = env_path.read_text()
    except OSError:
        return values
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def _upsert_env_value(env_path: Path, key: str, value: str) -> None:
    try:
        content = env_path.read_text()
        lines = content.splitlines()
    except OSError:
        lines = []
    updated = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(new_lines) + "\n")


def _extract_http_error_message(response: httpx.Response) -> str | None:
    if "application/json" not in response.headers.get("content-type", ""):
        return None
    try:
        response_data = response.json()
    except ValueError:
        return None
    if isinstance(response_data, dict):
        for key in ("message", "error"):
            value = response_data.get(key)
            if isinstance(value, str):
                return value
        detail = response_data.get("detail")
        if isinstance(detail, dict):
            detail_message = detail.get("message")
            if isinstance(detail_message, str):
                return detail_message
    return None


def _fetch_access_token(api_key: str, username: str, password: str) -> str:
    headers = DEFAULT_HEADERS | {"kptnkey": api_key}
    response = httpx.post(
        urljoin(DEFAULT_API_URL, "/auth/login"),
        json={"email": username, "password": password},
        headers=headers,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["accessToken"]


def _credential_from_command(command: str) -> str | None:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as exc:
        rprint(f"[red]Error executing password manager command: {exc}[/red]")
        if exc.stderr:
            rprint(f"[red]Command output: {exc.stderr}[/red]")
        return None
    except Exception as exc:
        rprint(f"[red]Unexpected error: {exc}[/red]")
        return None


def _get_credentials(
    username_command: str | None, password_command: str | None
) -> tuple[str | None, str | None]:
    username = None
    password = None
    if username_command:
        username = _credential_from_command(username_command)
        if username:
            rprint("[green]✓ Username retrieved from password manager[/green]")
    if password_command:
        password = _credential_from_command(password_command)
        if password:
            rprint("[green]✓ Password retrieved from password manager[/green]")
    if not username:
        username = Prompt.ask("Enter your kptncook email address")
    if not password:
        password = Prompt.ask("Enter your kptncook password", password=True)
    return username, password


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
    scaffolded = _scaffold_env_file(env_path)
    if scaffolded:
        rprint(f"Created {env_path} with a starter template.")

    values = _read_env_values(env_path)
    existing_api_key = values.get("KPTNCOOK_API_KEY", "").strip()
    if not existing_api_key:
        chosen_key = api_key or DEFAULT_API_KEY
        _upsert_env_value(env_path, "KPTNCOOK_API_KEY", chosen_key)
        rprint("Added KPTNCOOK_API_KEY to the .env file.")
    else:
        chosen_key = existing_api_key
        rprint("KPTNCOOK_API_KEY is already set.")

    if username_command:
        _upsert_env_value(env_path, "KPTNCOOK_USERNAME_COMMAND", username_command)
        rprint("Stored KPTNCOOK_USERNAME_COMMAND in the .env file.")
    if password_command:
        _upsert_env_value(env_path, "KPTNCOOK_PASSWORD_COMMAND", password_command)
        rprint("Stored KPTNCOOK_PASSWORD_COMMAND in the .env file.")

    if not fetch_access_token:
        rprint("Skipping access token fetch.")
        return

    values = _read_env_values(env_path)
    username, password = _get_credentials(
        username_command or values.get("KPTNCOOK_USERNAME_COMMAND"),
        password_command or values.get("KPTNCOOK_PASSWORD_COMMAND"),
    )

    if not username or not password:
        rprint("[red]Failed to get credentials.[/red]")
        sys.exit(1)

    try:
        access_token = _fetch_access_token(chosen_key, username, password)
        _upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", access_token)
        rprint("[green]✓ Access token retrieved successfully.[/green]")
        rprint(f"Saved KPTNCOOK_ACCESS_TOKEN to {env_path}.")
    except httpx.HTTPStatusError as exc:
        detail = _extract_http_error_message(exc.response)
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
        rprint(f"[red]Request failed: {exc}[/red]")
        sys.exit(1)
