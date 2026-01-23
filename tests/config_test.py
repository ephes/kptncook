from pathlib import Path

import pytest
from pydantic import ValidationError

from kptncook.config import Settings
from kptncook.env import ENV_TEMPLATE, scaffold_env_file


def test_root_must_exist_creates_dir(tmp_path, monkeypatch):
    root = tmp_path / "kptncook-home"
    monkeypatch.setenv("KPTNCOOK_API_KEY", "test-key")
    monkeypatch.setenv("KPTNCOOK_HOME", str(root))

    settings = Settings()

    assert isinstance(settings.root, Path)
    assert settings.root == root
    assert root.is_dir()


def test_root_must_exist_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KPTNCOOK_API_KEY", "test-key")
    monkeypatch.setenv("KPTNCOOK_HOME", "~/kptncook-home")

    settings = Settings()

    expected = tmp_path / "kptncook-home"
    assert settings.root == expected
    assert expected.is_dir()


def test_api_key_rejects_empty_string(tmp_path, monkeypatch):
    monkeypatch.setenv("KPTNCOOK_API_KEY", "   ")
    monkeypatch.setenv("KPTNCOOK_HOME", str(tmp_path))

    with pytest.raises(ValidationError):
        Settings()


def test_scaffold_env_file_creates_template(tmp_path):
    env_path = tmp_path / ".kptncook" / ".env"

    result = scaffold_env_file(env_path)

    assert result is True
    assert env_path.exists()
    assert env_path.read_text() == ENV_TEMPLATE


def test_scaffold_env_file_skips_when_non_empty(tmp_path):
    env_path = tmp_path / ".kptncook" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("KPTNCOOK_API_KEY=test-key\n")

    result = scaffold_env_file(env_path)

    assert result is False
    assert env_path.read_text() == "KPTNCOOK_API_KEY=test-key\n"
