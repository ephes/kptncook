import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import kptncook.config as config
from kptncook.config import Settings, SettingsError, clear_settings_cache, get_settings
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


def test_get_settings_is_lazy_and_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("KPTNCOOK_API_KEY", "cached-key")
    monkeypatch.setenv("KPTNCOOK_HOME", str(tmp_path / "home"))
    clear_settings_cache()

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.kptncook_api_key == "cached-key"


def test_get_settings_raises_settings_error_and_scaffolds_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".kptncook" / ".env"
    monkeypatch.delenv("KPTNCOOK_API_KEY", raising=False)
    monkeypatch.setenv("KPTNCOOK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.setitem(Settings.model_config, "env_file", env_path)
    clear_settings_cache()

    with pytest.raises(SettingsError) as exc_info:
        get_settings()

    error = exc_info.value
    assert "kptncook_api_key" in error.missing_fields
    assert error.scaffolded is True
    assert error.env_path == env_path
    assert env_path.read_text() == ENV_TEMPLATE


def test_importing_runtime_modules_without_config_does_not_exit(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"KPTNCOOK_API_KEY", "KPTNCOOK_ACCESS_TOKEN", "KPTNCOOK_HOME"}
    }
    env["HOME"] = str(tmp_path)
    env["PYTHONPATH"] = str(repo_root / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import kptncook.config; "
                "import kptncook.api; "
                "import kptncook.mealie; "
                "import kptncook.services.workflows; "
                "import kptncook; "
                "print('ok')"
            ),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_settings_proxy_raises_attribute_error_for_missing_required_field():
    clear_settings_cache()

    with pytest.raises(AttributeError):
        _ = config.settings.kptncook_api_key
