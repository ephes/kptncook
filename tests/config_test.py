import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dotenv.main import DotEnv
from pydantic import ValidationError
from typer.testing import CliRunner

import kptncook.config as config
from kptncook import env
from kptncook.cli import app
from kptncook.config import Settings, SettingsError, clear_settings_cache, get_settings
from kptncook.env import ENV_TEMPLATE, MAX_ENV_BYTES, scaffold_env_file


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


@pytest.mark.parametrize("explicit", [False, True])
def test_settings_refuse_oversized_dotenv_before_parsing(
    tmp_path, monkeypatch, explicit
):
    env_path = tmp_path / "private-file-name"
    env_path.write_text("MEALIE_PASSWORD=" + "x" * MAX_ENV_BYTES)
    parse = Mock(return_value={})
    monkeypatch.setattr(DotEnv, "dict", parse)
    overrides = {"_env_file": env_path} if explicit else {}
    if not explicit:
        monkeypatch.setitem(Settings.model_config, "env_file", env_path)

    with pytest.raises(SettingsError) as caught:
        Settings(**overrides)

    parse.assert_not_called()
    assert "64 KiB" in " ".join(caught.value.diagnostics)
    assert "private-file-name" not in " ".join(caught.value.diagnostics)


def test_cli_refuses_invalid_dotenv_before_constructing_client(tmp_path, monkeypatch):
    env_path = tmp_path / "private-file-name"
    env_path.write_bytes(b"KPTNCOOK_API_KEY=private-value\xff")
    monkeypatch.setitem(Settings.model_config, "env_file", env_path)
    create_client = Mock(side_effect=AssertionError("HTTP client constructed"))
    monkeypatch.setattr("kptncook.http_client.httpx.Client", create_client)

    result = CliRunner().invoke(app, ["kptncook-today"])

    assert result.exit_code == 1
    assert "Invalid configuration" in result.output
    assert "64 KiB" in result.output
    assert "private" not in result.output
    create_client.assert_not_called()


@pytest.mark.parametrize(
    "interruption", ["in-place edit", "in-place edit, mtime restored", "short read"]
)
def test_runtime_rejects_incomplete_dotenv_before_parsing(
    tmp_path, monkeypatch, mocker, interruption
):
    env_path = tmp_path / ".env"
    old_key = b"KPTNCOOK_API_KEY=synthetic-old\n"
    new_key = b"KPTNCOOK_API_KEY=synthetic-new\n"
    env_path.write_bytes(old_key + b"KPTNCOOK_LANG=fr\n")
    monkeypatch.delenv("KPTNCOOK_API_KEY")
    monkeypatch.delenv("KPTNCOOK_LANG", raising=False)
    parse = mocker.spy(DotEnv, "dict")
    original_fdopen = env.os.fdopen

    def interrupt_read(fd, mode):
        handle = original_fdopen(fd, mode, buffering=0)
        read = handle.read

        def interrupted_read(limit):
            first_line = read(len(old_key))
            if interruption == "short read":
                return first_line
            before = env_path.stat()
            env_path.write_bytes(new_key + b"KPTNCOOK_LANG=es\n")
            if interruption == "in-place edit, mtime restored":
                # Only the ctime check can notice this edit.
                os.utime(
                    env_path, ns=(before.st_atime_ns, before.st_mtime_ns)
                )
            else:
                os.utime(env_path, (1, 1))
            return first_line + read(limit - len(first_line))

        handle.read = interrupted_read
        return handle

    with monkeypatch.context() as patch:
        patch.setattr(env.os, "fdopen", interrupt_read)
        with pytest.raises(SettingsError) as caught:
            get_settings()

    parse.assert_not_called()
    assert caught.value.diagnostics == (env.ENV_READ_ERROR,)
    loaded = get_settings()
    if interruption == "short read":
        assert loaded.kptncook_api_key == "synthetic-old"
        assert loaded.kptncook_lang == "fr"
    else:
        assert loaded.kptncook_api_key == "synthetic-new"
        assert loaded.kptncook_lang == "es"


def test_runtime_accepts_open_dotenv_during_atomic_replacement(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("KPTNCOOK_API_KEY=synthetic-old\n")
    replacement = tmp_path / "new.env"
    replacement.write_text("KPTNCOOK_API_KEY=synthetic-new\n")
    monkeypatch.delenv("KPTNCOOK_API_KEY")
    inode = env_path.stat().st_ino
    original_fstat = env.os.fstat

    def replace_after_stat(fd):
        info = original_fstat(fd)
        if info.st_ino == inode and replacement.exists():
            replacement.replace(env_path)
        return info

    monkeypatch.setattr(env.os, "fstat", replace_after_stat)
    assert get_settings().kptncook_api_key == "synthetic-old"
    assert not replacement.exists()
    clear_settings_cache()
    assert get_settings().kptncook_api_key == "synthetic-new"


def test_settings_preserve_dotenv_mapping_and_precedence(tmp_path, monkeypatch):
    env_path = tmp_path / "selected.env"
    env_path.write_text(
        "kptncook_api_key=file-key\n"
        "SYNTHETIC_LANG=fr\n"
        "KPTNCOOK_LANG=${SYNTHETIC_LANG}\n"
        "KPTNCOOK_HOME=" + str(tmp_path / "file-home") + "\n"
    )
    default_path = tmp_path / "default.env"
    default_path.write_text("KPTNCOOK_LANG=es\n")
    monkeypatch.setitem(Settings.model_config, "env_file", default_path)
    monkeypatch.delenv("KPTNCOOK_LANG", raising=False)
    monkeypatch.delenv("KPTNCOOK_HOME")
    monkeypatch.delenv("KPTNCOOK_API_KEY")

    from_file = Settings(_env_file=env_path)
    assert from_file.kptncook_api_key == "file-key"
    assert from_file.kptncook_lang == "fr"
    assert from_file.root == tmp_path / "file-home"

    monkeypatch.setenv("KPTNCOOK_API_KEY", "shell-key")
    assert Settings(_env_file=env_path).kptncook_api_key == "shell-key"
    assert (
        Settings(_env_file=env_path, kptncook_api_key="init-key").kptncook_api_key
        == "init-key"
    )
    assert Settings(KPTNCOOK_HOME=tmp_path).kptncook_lang == "es"
    assert Settings(_env_file=None, KPTNCOOK_HOME=tmp_path).kptncook_lang == "de"


@pytest.mark.parametrize("rebuild_before_release", [True, False])
def test_invalidation_fences_inflight_settings(monkeypatch, rebuild_before_release):
    started = Event()
    release = Event()
    retired = SimpleNamespace(kptncook_api_key="retired-key")
    current = SimpleNamespace(kptncook_api_key="current-key", kptncook_lang="de")
    build = Mock(side_effect=[retired, current])

    def construct():
        candidate = build()
        if candidate is retired:
            started.set()
            assert release.wait(10)
        return candidate

    monkeypatch.setattr(config, "Settings", construct)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(get_settings)
        try:
            assert started.wait(10)
            clear_settings_cache()
            if rebuild_before_release:
                assert get_settings() is current
            monkeypatch.setattr(config.settings, "kptncook_lang", "fr")
        finally:
            release.set()
        assert pending.result(timeout=10) is current

    assert get_settings() is current
    assert config.settings.kptncook_api_key == "current-key"
    assert config.settings.kptncook_lang == current.kptncook_lang == "fr"
    assert build.call_count == 2


@pytest.mark.parametrize(
    ("invalidate", "publish"), [(True, True), (True, False), (False, True)]
)
def test_get_settings_recovers_from_superseded_failure(
    monkeypatch, tmp_path, invalidate, publish
):
    started = Event()
    release = Event()
    current = Settings()
    error = SettingsError(
        None,
        missing_fields={"kptncook_api_key"},
        env_path=tmp_path / ".env",
        scaffolded=False,
    )

    load = Mock(side_effect=[error, current])

    def construct():
        try:
            return load()
        except SettingsError:
            started.set()
            assert release.wait(10)
            raise

    monkeypatch.setattr(config, "_load_settings", construct)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(get_settings)
        try:
            assert started.wait(10)
            if invalidate:
                clear_settings_cache()
            if publish:
                assert get_settings() is current
        finally:
            release.set()
        assert pending.result(timeout=10) is current

    assert get_settings() is current
    assert load.call_count == 2
