"""
Tests for password manager integration.
"""

import subprocess
from unittest.mock import MagicMock

from kptncook.password_manager import get_credential_from_command, get_credentials


class TestGetCredentialFromCommand:
    """Test get_credential_from_command function."""

    def test_successful_command(self, mocker):
        """Test successful command execution."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = MagicMock(
            stdout="test_password\n", stderr="", returncode=0
        )

        result = get_credential_from_command("echo test_password")

        assert result == "test_password"
        mock_run.assert_called_once_with(
            "echo test_password", shell=True, capture_output=True, text=True, check=True
        )

    def test_command_failure(self, mocker):
        """Test command execution failure."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "fake_command", stderr="Command not found"
        )

        result = get_credential_from_command("fake_command")

        assert result is None

    def test_unexpected_error(self, mocker):
        """Test unexpected error handling."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.side_effect = Exception("Unexpected error")

        result = get_credential_from_command("some_command")

        assert result is None


class TestGetCredentials:
    """Test get_credentials function."""

    def test_both_commands_successful(self, mocker):
        """Test when both username and password commands succeed."""
        mock_run = mocker.patch("subprocess.run")
        # First call for username, second for password
        mock_run.side_effect = [
            MagicMock(stdout="test@example.com\n"),
            MagicMock(stdout="secret_password\n"),
        ]

        username, password = get_credentials(
            username_command="op read username",
            password_command="op read password",
            interactive_fallback=False,
        )

        assert username == "test@example.com"
        assert password == "secret_password"
        assert mock_run.call_count == 2

    def test_no_commands_no_fallback(self):
        """Test when no commands are provided and no fallback."""
        username, password = get_credentials(
            username_command=None, password_command=None, interactive_fallback=False
        )

        assert username is None
        assert password is None

    def test_command_fails_with_fallback(self, mocker):
        """Test fallback to interactive prompt when command fails."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.side_effect = subprocess.CalledProcessError(1, "cmd")

        mock_prompt = mocker.patch("rich.prompt.Prompt.ask")
        mock_prompt.side_effect = ["fallback@example.com", "fallback_password"]

        username, password = get_credentials(
            username_command="failing_command",
            password_command="failing_command",
            interactive_fallback=True,
        )

        assert username == "fallback@example.com"
        assert password == "fallback_password"
        assert mock_prompt.call_count == 2

    def test_partial_success_with_fallback(self, mocker):
        """Test when only username command succeeds."""
        mock_run = mocker.patch("subprocess.run")
        # Username succeeds, password fails
        mock_run.side_effect = [
            MagicMock(stdout="test@example.com\n"),
            subprocess.CalledProcessError(1, "cmd"),
        ]

        mock_prompt = mocker.patch("rich.prompt.Prompt.ask")
        mock_prompt.return_value = "fallback_password"

        username, password = get_credentials(
            username_command="op read username",
            password_command="failing_command",
            interactive_fallback=True,
        )

        assert username == "test@example.com"
        assert password == "fallback_password"
        # Only password should be prompted
        mock_prompt.assert_called_once_with(
            "Enter your kptncook password", password=True
        )
