"""Synthetic credential publication outcomes at the directory-sync boundary."""

import errno
import os
import stat
import traceback
from importlib import import_module

import portalocker
import pytest
from typer.testing import CliRunner

from kptncook import config, env, setup

SECRET = "synthetic-credential-r109"


def fail_directory_sync(monkeypatch, error_number):
    sync = os.fsync
    calls = []

    def injected(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            calls.append(fd)
            raise OSError(error_number, SECRET)
        return sync(fd)

    monkeypatch.setattr(os, "fsync", injected)
    return calls


@pytest.mark.parametrize("operation", ["upsert", "scaffold"])
@pytest.mark.parametrize("error_number", [errno.EIO, errno.ENOSPC])
def test_directory_sync_failure_reports_published_and_keeps_private_bytes(
    tmp_path, monkeypatch, operation, error_number
):
    path = tmp_path / ".env"
    if operation == "upsert":
        path.write_text("KEEP=synthetic\nKPTNCOOK_ACCESS_TOKEN=old\n")
    calls = fail_directory_sync(monkeypatch, error_number)
    with pytest.raises(OSError, match="published.*durability.*uncertain") as caught:
        if operation == "upsert":
            env.upsert_env_value(path, "KPTNCOOK_ACCESS_TOKEN", SECRET)
        else:
            env.scaffold_env_file(path)
    assert len(calls) == 1
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    if operation == "upsert":
        assert path.read_text() == f"KEEP=synthetic\nKPTNCOOK_ACCESS_TOKEN='{SECRET}'\n"
    else:
        assert path.read_text() == env.ENV_TEMPLATE
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["..env.lock", ".env"]
    with portalocker.Lock(tmp_path / "..env.lock", timeout=0):
        pass


@pytest.mark.parametrize("error_number", [errno.EINVAL, errno.ENOSYS, errno.ENOTSUP])
def test_unsupported_directory_sync_allows_complete_update(
    tmp_path, monkeypatch, error_number
):
    path = tmp_path / ".env"
    calls = fail_directory_sync(monkeypatch, error_number)
    env.upsert_env_value(path, "KPTNCOOK_ACCESS_TOKEN", SECRET)
    assert len(calls) == 1
    assert env.read_env_values(path)["KPTNCOOK_ACCESS_TOKEN"] == SECRET


def test_success_syncs_resolved_credential_parent(tmp_path, monkeypatch):
    target = tmp_path / "real" / "credentials"
    target.parent.mkdir()
    alias = tmp_path / ".env"
    alias.symlink_to(target)
    sync = os.fsync
    parents = []

    def record(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            parents.append((info.st_dev, info.st_ino))
        return sync(fd)

    monkeypatch.setattr(os, "fsync", record)
    env.upsert_env_value(alias, "KPTNCOOK_ACCESS_TOKEN", SECRET)
    info = target.parent.stat()
    assert parents == [(info.st_dev, info.st_ino)]
    assert alias.is_symlink()


@pytest.mark.parametrize(
    "command", ["setup-scaffold", "setup-token", "refresh", "first-run"]
)
def test_cli_reports_uncertain_publication_without_claiming_success(
    tmp_path, monkeypatch, command
):
    path = tmp_path / ".env"
    cli = import_module("kptncook.cli")
    monkeypatch.setattr(cli, "ENV_PATH", path)
    monkeypatch.setattr(config, "ENV_PATH", path)
    if command in {"setup-token", "refresh"}:
        path.write_text("KPTNCOOK_API_KEY=synthetic-key\n")
    fail_directory_sync(monkeypatch, errno.EIO)
    if command.startswith("setup"):
        monkeypatch.setattr(
            setup, "get_credentials", lambda *a: ("synthetic", "synthetic")
        )
        monkeypatch.setattr(setup, "_fetch_access_token", lambda *a: SECRET)
        args = ["--env-path", str(path)]
        if command == "setup-scaffold":
            args.append("--no-fetch-access-token")
        result = CliRunner().invoke(setup.cli, args)
    elif command == "refresh":
        monkeypatch.setattr(cli, "get_kptncook_access_token_workflow", lambda: SECRET)
        result = CliRunner().invoke(cli.app, ["kptncook-access-token"])
    else:
        monkeypatch.delenv("KPTNCOOK_API_KEY")
        result = CliRunner().invoke(cli.app, ["list-recipes"])
    assert result.exit_code == 1
    assert "published" in result.output
    assert "durability is uncertain" in result.output
    assert SECRET not in result.output
    assert "successfully" not in result.output
    assert "Saved KPTNCOOK_ACCESS_TOKEN" not in result.output
    assert path.exists()
