import os
import stat

from pathlib import Path

from kptncook.env import ENV_TEMPLATE as MAIN_ENV_TEMPLATE
from kptncook.env import (
    _default_env_dir,
    read_env_values,
    scaffold_env_file,
    upsert_env_value,
)
from kptncook_setup import ENV_TEMPLATE as SETUP_ENV_TEMPLATE


def test_read_env_values_skips_comments(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment\nKPTNCOOK_API_KEY=abc123\n\nKPTNCOOK_ACCESS_TOKEN=token\n"
    )

    values = read_env_values(env_path)

    assert values["KPTNCOOK_API_KEY"] == "abc123"
    assert values["KPTNCOOK_ACCESS_TOKEN"] == "token"


def test_upsert_env_value_updates_existing(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("KPTNCOOK_API_KEY=old\n")

    upsert_env_value(env_path, "KPTNCOOK_API_KEY", "new")

    assert env_path.read_text() == "KPTNCOOK_API_KEY=new\n"


def test_upsert_env_value_appends_missing(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("KPTNCOOK_API_KEY=abc\n")

    upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", "token")

    assert (
        env_path.read_text() == "KPTNCOOK_API_KEY=abc\n\nKPTNCOOK_ACCESS_TOKEN=token\n"
    )


def test_scaffold_env_file_sets_owner_only_permissions(tmp_path):
    env_path = tmp_path / ".env"

    assert scaffold_env_file(env_path) is True

    if os.name != "nt":
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_upsert_env_value_tightens_permissions(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("KPTNCOOK_API_KEY=abc\n")
    if os.name != "nt":
        env_path.chmod(0o644)

    upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", "token")

    if os.name != "nt":
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_env_templates_are_kept_in_sync():
    assert MAIN_ENV_TEMPLATE == SETUP_ENV_TEMPLATE


def test_default_env_dir_honors_kptncook_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KPTNCOOK_HOME", str(tmp_path / "data"))

    assert _default_env_dir() == tmp_path / "data"


def test_default_env_dir_expands_kptncook_home(monkeypatch):
    monkeypatch.setenv("KPTNCOOK_HOME", "~/kptncook-home")

    assert _default_env_dir() == Path.home() / "kptncook-home"


def test_default_env_dir_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("KPTNCOOK_HOME", raising=False)

    assert _default_env_dir() == Path.home() / ".kptncook"


def _capture_replace(monkeypatch):
    """Record the temp file's mode and content at the moment it is moved."""
    import kptncook.env as env_module

    seen: dict[str, object] = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["mode"] = stat.S_IMODE(os.stat(src).st_mode)
        seen["content"] = Path(src).read_text()
        seen["src_dir"] = Path(src).parent
        real_replace(src, dst)

    monkeypatch.setattr(env_module.os, "replace", spy)
    return seen


def test_upsert_env_value_temp_file_is_owner_only_before_replace(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    seen = _capture_replace(monkeypatch)
    old_umask = os.umask(0o022)
    try:
        upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", "secret")
    finally:
        os.umask(old_umask)

    assert seen["content"] == "KPTNCOOK_ACCESS_TOKEN=secret\n"
    assert seen["src_dir"] == tmp_path
    if os.name != "nt":
        assert seen["mode"] == 0o600
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_scaffold_env_file_temp_file_is_owner_only_before_replace(
    tmp_path, monkeypatch
):
    env_path = tmp_path / ".env"
    seen = _capture_replace(monkeypatch)

    assert scaffold_env_file(env_path) is True

    assert seen["content"] == MAIN_ENV_TEMPLATE
    if os.name != "nt":
        assert seen["mode"] == 0o600


def test_upsert_env_value_failure_leaves_old_file_unchanged(tmp_path, monkeypatch):
    import kptncook.env as env_module

    env_path = tmp_path / ".env"
    original = "KPTNCOOK_API_KEY=abc\nMEALIE_PASSWORD=keep-me\n"
    env_path.write_text(original)

    def failing_fsync(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(env_module.os, "fsync", failing_fsync)

    try:
        upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", "token")
    except OSError:
        pass
    else:
        raise AssertionError("expected the write failure to propagate")

    assert env_path.read_text() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_scaffold_env_file_failure_returns_false_and_cleans_up(tmp_path, monkeypatch):
    import kptncook.env as env_module

    env_path = tmp_path / ".env"

    def failing_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(env_module.os, "replace", failing_replace)

    assert scaffold_env_file(env_path) is False
    assert list(tmp_path.iterdir()) == []


def test_upsert_env_value_keeps_other_keys(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment\nKPTNCOOK_API_KEY=abc\nMEALIE_PASSWORD=pw\n"
        "KPTNCOOK_ACCESS_TOKEN=old\nMEALIE_API_TOKEN=tok\n"
    )

    upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", "new")

    assert env_path.read_text() == (
        "# comment\nKPTNCOOK_API_KEY=abc\nMEALIE_PASSWORD=pw\n"
        "KPTNCOOK_ACCESS_TOKEN=new\nMEALIE_API_TOKEN=tok\n"
    )


def test_upsert_env_value_does_not_replace_unreadable_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    original = "MEALIE_PASSWORD=keep-me\n"
    env_path.write_text(original)
    real_read_text = Path.read_text

    def failing_read_text(self, *args, **kwargs):
        if self == env_path:
            raise PermissionError("denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read_text)

    try:
        upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", "token")
    except PermissionError:
        pass
    else:
        raise AssertionError("expected the read error to propagate")

    monkeypatch.setattr(Path, "read_text", real_read_text)
    assert env_path.read_text() == original


def test_upsert_env_value_keeps_symlink(tmp_path):
    if os.name == "nt":
        return
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_env = real_dir / "kptncook.env"
    real_env.write_text("KPTNCOOK_API_KEY=abc\n")
    env_path = tmp_path / ".env"
    env_path.symlink_to(real_env)

    upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", "token")

    assert env_path.is_symlink()
    assert (
        real_env.read_text() == "KPTNCOOK_API_KEY=abc\n\nKPTNCOOK_ACCESS_TOKEN=token\n"
    )
    assert stat.S_IMODE(real_env.stat().st_mode) == 0o600
