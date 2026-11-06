"""
Environment configuration helpers for kptncook.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path

import portalocker
from dotenv import dotenv_values, set_key

DEFAULT_API_KEY = "6q7QNKy-oIgk-IMuWisJ-jfN7s6"
ENV_FILE_MODE = 0o600


def _default_env_dir() -> Path:
    """Resolve the directory that holds the ``.env`` file.

    Honors ``KPTNCOOK_HOME`` so the config file lands in the same directory as
    the rest of kptncook's data (matching ``Settings.root``). This keeps the
    scaffolded ``.env`` and the file the app reads in sync, e.g. inside the
    Docker image where ``KPTNCOOK_HOME=/data`` is the mounted volume.
    """
    home = os.environ.get("KPTNCOOK_HOME")
    if home:
        return Path(os.path.expandvars(home)).expanduser()
    return Path.home() / ".kptncook"


ENV_PATH = _default_env_dir() / ".env"
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


def _tighten_env_permissions(env_path: Path) -> None:
    """Best-effort hardening for secret-bearing env files."""
    try:
        env_path.chmod(ENV_FILE_MODE)
    except OSError:
        pass


def _atomic_write_env(
    env_path: Path, content: str, *, assignment: tuple[str, str] | None = None
) -> None:
    """Replace ``env_path`` with ``content`` without a window of exposure or loss.

    The content goes to a temporary file in the same directory that is created
    owner-only (``mkstemp`` uses ``O_CREAT | O_EXCL`` and mode 0600), is fsynced
    and then moved over the target with ``os.replace``. Readers see either the
    old file or the complete new one, never an empty or partial file, and the
    secrets are never readable by other users. If ``env_path`` is a symlink the
    file it points to is replaced, so the link itself is kept.
    """
    target = Path(os.path.realpath(env_path)) if env_path.is_symlink() else env_path
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        if assignment is not None:
            key, value = assignment
            # set_key escapes single quotes; also escape backslashes because
            # python-dotenv decodes doubled backslashes in single-quoted values.
            set_key(tmp_path, key, value.replace("\\", "\\\\"), quote_mode="always")
        with tmp_path.open("r+", encoding="utf-8") as handle:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), ENV_FILE_MODE)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    _fsync_directory(target.parent)


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync of a directory so a rename survives a crash."""
    if os.name == "nt":
        return
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


@contextmanager
def _locked_env_path(env_path: Path) -> Iterator[Path]:
    """Serialize cooperating writers on a stable sibling of the resolved target.

    Never remove the lock file: waiting writers must all lock the same inode,
    even though atomic publication replaces the env file's inode.
    """
    target = env_path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, ENV_FILE_MODE)
    with os.fdopen(fd, "a") as handle:
        try:
            portalocker.lock(handle, portalocker.LOCK_EX)
            try:
                yield target
            finally:
                portalocker.unlock(handle)
        except portalocker.exceptions.LockException as exc:
            raise OSError("Could not lock configuration file") from exc


def scaffold_env_file(env_path: Path = ENV_PATH) -> bool:
    try:
        with _locked_env_path(env_path) as target:
            if target.exists() and target.stat().st_size > 0:
                return False
            _atomic_write_env(target, ENV_TEMPLATE)
            _tighten_env_permissions(target)
    except OSError:
        return False
    return True


def read_env_values(env_path: Path) -> dict[str, str]:
    try:
        content = env_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return {
        key: value
        for key, value in dotenv_values(stream=StringIO(content)).items()
        if value is not None
    }


def upsert_env_value(env_path: Path, key: str, value: str) -> None:
    """Set ``key`` in the env file, keeping every other line.

    Only a missing file counts as empty. Any other read error is raised so an
    unreadable file is never replaced by one that holds just ``key``.
    """
    with _locked_env_path(env_path) as target:
        try:
            content = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ""
        _atomic_write_env(target, content, assignment=(key, value))
        _tighten_env_permissions(target)
