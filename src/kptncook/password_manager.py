"""
Password manager integration for retrieving credentials.
"""

import subprocess

from rich import print as rprint


def get_credential_from_command(command: str) -> str | None:
    """
    Execute a shell command to retrieve a credential from a password manager.

    Args:
        command: Shell command to execute (e.g., "op read op://vault/item/field")

    Returns:
        The credential as a string, or None if the command fails
    """
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        rprint(f"[red]Error executing password manager command: {e}[/red]")
        rprint(f"[red]Command output: {e.stderr}[/red]")
        return None
    except Exception as e:
        rprint(f"[red]Unexpected error: {e}[/red]")
        return None


def get_credentials(
    username_command: str | None = None,
    password_command: str | None = None,
    interactive_fallback: bool = True,
) -> tuple[str | None, str | None]:
    """
    Get username and password from password manager or interactive prompt.

    Args:
        username_command: Command to retrieve username
        password_command: Command to retrieve password
        interactive_fallback: Whether to fall back to interactive prompt

    Returns:
        Tuple of (username, password) or (None, None) if retrieval fails
    """
    username = None
    password = None

    # Try to get credentials from password manager
    if username_command:
        username = get_credential_from_command(username_command)
        if username:
            rprint("[green]✓ Username retrieved from password manager[/green]")

    if password_command:
        password = get_credential_from_command(password_command)
        if password:
            rprint("[green]✓ Password retrieved from password manager[/green]")

    # Fall back to interactive prompt if needed
    if interactive_fallback:
        if not username:
            from rich.prompt import Prompt

            username = Prompt.ask("Enter your kptncook email address")

        if not password:
            from rich.prompt import Prompt

            password = Prompt.ask("Enter your kptncook password", password=True)

    return username, password
