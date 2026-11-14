"""Synthetic credential files and stable lock inode admission."""

import os
from pathlib import Path

import pytest

from kptncook import env


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "hardlink"])
def test_config_lock_refuses_unsafe_file(tmp_path, kind):
    config = tmp_path / ".env"
    config.write_text("KEEP=synthetic\n")
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("outside bytes")
    lock = tmp_path / "..env.lock"
    if kind == "symlink":
        lock.symlink_to(sentinel)
    elif kind == "fifo":
        os.mkfifo(lock)
    elif kind == "directory":
        lock.mkdir()
    else:
        os.link(sentinel, lock)
    with pytest.raises(OSError):
        env.upsert_env_value(config, "NEW", "value")
    assert config.read_text() == "KEEP=synthetic\n"
    assert sentinel.read_text() == "outside bytes"


def test_config_lock_refuses_replaced_named_inode(tmp_path, monkeypatch):
    import threading
    import portalocker

    config = tmp_path / ".env"
    config.write_text("KEEP=synthetic\n")
    lock = tmp_path / "..env.lock"
    opened = threading.Event()
    original_open = env.os.open
    descriptors, errors = [], []

    def observe(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if Path(path) == lock:
            descriptors.append(fd)
            opened.set()
        return fd

    def update():
        try:
            env.upsert_env_value(config, "NEW", "value")
        except OSError as exc:
            errors.append(exc)

    with portalocker.Lock(lock, timeout=0):
        monkeypatch.setattr(env.os, "open", observe)
        waiter = threading.Thread(target=update, daemon=True)
        waiter.start()
        assert opened.wait(2)
        lock.rename(tmp_path / "retired-lock")
        lock.touch()
    waiter.join(2)
    assert not waiter.is_alive()
    assert len(errors) == 1
    assert "lock file changed" in str(errors[0])
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert config.read_text() == "KEEP=synthetic\n"


@pytest.mark.parametrize("operation", ["read", "upsert", "setup"])
@pytest.mark.parametrize("kind", ["oversized", "invalid-utf8", "directory", "fifo"])
def test_config_source_refusal_preserves_bytes(tmp_path, operation, kind):
    from typer.testing import CliRunner
    from kptncook.setup import cli

    config = tmp_path / ".env"
    if kind == "directory":
        config.mkdir()
    elif kind == "fifo":
        os.mkfifo(config)
    else:
        config.write_bytes(b"#" * (64 * 1024 + 1) if kind == "oversized" else b"\xff")
    before = config.read_bytes() if kind in ("oversized", "invalid-utf8") else None
    if operation == "setup":
        result = CliRunner().invoke(
            cli, ["--env-path", str(config), "--no-fetch-access-token"]
        )
        assert result.exit_code == 1
        assert "Could not read configuration" in result.output
    else:
        with pytest.raises(OSError, match="Could not read configuration"):
            if operation == "read":
                env.read_env_values(config)
            else:
                env.upsert_env_value(config, "NEW", "value")
    if before is not None:
        assert config.read_bytes() == before


def test_config_source_exact_limit_and_missing(tmp_path):
    config = tmp_path / ".env"
    assert env.read_env_values(config) == {}
    config.write_bytes(b"KEY=value\n#" + b"x" * (64 * 1024 - 11))
    assert config.stat().st_size == 64 * 1024
    assert env.read_env_values(config) == {"KEY": "value"}
    alias = tmp_path / "alias"
    alias.symlink_to(config)
    assert env.read_env_values(alias) == {"KEY": "value"}


def test_config_source_growth_after_open_is_bounded(tmp_path, monkeypatch):
    config = tmp_path / ".env"
    config.write_text("KEY=value\n")
    original_fstat = env.os.fstat

    def grow(fd):
        result = original_fstat(fd)
        with config.open("ab") as handle:
            handle.write(b"#" * (64 * 1024))
        return result

    monkeypatch.setattr(env.os, "fstat", grow)
    with pytest.raises(OSError, match="Could not read configuration"):
        env.read_env_values(config)


def test_unreadable_config_is_not_missing(tmp_path, monkeypatch):
    config = tmp_path / ".env"
    original_open = env.os.open

    def denied(path, *args, **kwargs):
        if Path(path) == config:
            raise PermissionError("synthetic denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(env.os, "open", denied)
    with pytest.raises(OSError, match="Could not read configuration"):
        env.read_env_values(config)


def test_config_lock_wait_is_bounded_and_retry_succeeds(tmp_path):
    import subprocess
    import sys
    import time

    config = tmp_path / ".env"
    config.write_text("KEEP=synthetic\n")
    holder = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import portalocker, sys; "
            "lock = portalocker.Lock(sys.argv[1], timeout=0); lock.acquire(); "
            "print('ready'); sys.stdin.readline(); lock.release()",
            str(tmp_path / "..env.lock"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    waiter_code = (
        "from pathlib import Path; import sys; from kptncook import env; "
        "env.ENV_LOCK_TIMEOUT_SECONDS = 0.15; "
        "env.upsert_env_value(Path(sys.argv[1]), 'NEW', 'value')"
    )
    try:
        assert holder.stdout.readline().strip() == "ready"
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-c", waiter_code, str(config)],
            capture_output=True,
            text=True,
            timeout=2,
        )
        assert time.monotonic() - started < 2
        assert result.returncode != 0
        assert "Configuration is busy" in result.stderr
        assert "synthetic" not in result.stderr
        assert config.read_text() == "KEEP=synthetic\n"
    finally:
        holder.communicate("release\n", timeout=5)
    result = subprocess.run(
        [sys.executable, "-c", waiter_code, str(config)],
        capture_output=True,
        text=True,
        timeout=2,
    )
    assert result.returncode == 0, result.stderr
    assert env.read_env_values(config) == {"KEEP": "synthetic", "NEW": "value"}


def test_config_lock_interruption_closes_waiting_descriptor(tmp_path, monkeypatch):
    import time
    import portalocker

    config = tmp_path / ".env"
    config.write_text("KEEP=synthetic\n")
    lock = tmp_path / "..env.lock"
    original_open = env.os.open
    descriptors = []

    def observe(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if Path(path) == lock:
            descriptors.append(fd)
        return fd

    def interrupt(_):
        raise KeyboardInterrupt

    with portalocker.Lock(lock, timeout=0):
        monkeypatch.setattr(env.os, "open", observe)
        monkeypatch.setattr(time, "sleep", interrupt)
        with pytest.raises(KeyboardInterrupt):
            env.upsert_env_value(config, "NEW", "value")
        with pytest.raises(OSError):
            os.fstat(descriptors[0])
        assert config.read_text() == "KEEP=synthetic\n"
    env.upsert_env_value(config, "NEW", "value")
    assert env.read_env_values(config)["NEW"] == "value"


def test_setup_reports_busy_configuration_once(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from typer.testing import CliRunner

    from kptncook import env, setup

    env_path = tmp_path / ".env"
    calls = []

    @contextmanager
    def busy(path):
        calls.append(path)
        raise env.ConfigurationBusyError(env.ENV_BUSY_MESSAGE)
        yield  # pragma: no cover

    monkeypatch.setattr(env, "_locked_env_path", busy)
    result = CliRunner().invoke(
        setup.cli, ["--env-path", str(env_path)], input="\n\n\n"
    )
    assert result.exit_code == 1
    assert "Configuration is busy" in result.output
    assert len(calls) == 1
