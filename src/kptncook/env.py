"""
Environment configuration helpers for kptncook.
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path

import portalocker
from dotenv import dotenv_values, set_key

DEFAULT_API_KEY = "6q7QNKy-oIgk-IMuWisJ-jfN7s6"
ENV_FILE_MODE = 0o600
MAX_ENV_BYTES = 64 * 1024
ENV_LOCK_TIMEOUT_SECONDS = 30.0


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
    opened: os.stat_result | None = None

    def open_lock(path: str, flags: int) -> int:
        nonlocal opened
        if lock_path.is_symlink():
            raise OSError("Unsafe configuration lock file")
        fd = os.open(
            path,
            flags | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
            ENV_FILE_MODE,
        )
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise OSError("Unsafe configuration lock file")
            return fd
        except BaseException:
            os.close(fd)
            raise

    lock = portalocker.Lock(
        lock_path,
        mode="a",
        timeout=ENV_LOCK_TIMEOUT_SECONDS,
        check_interval=0.05,
        flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        opener=open_lock,
    )
    try:
        handle = lock.acquire()
    except portalocker.exceptions.AlreadyLocked:
        raise OSError(
            "Configuration is busy; retry after the current writer finishes"
        ) from None
    except portalocker.exceptions.LockException:
        raise OSError("Could not lock configuration file") from None
    try:
        assert opened is not None
        named = lock_path.lstat()
        current = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or current.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise OSError("Configuration lock file changed")
        yield target
    finally:
        lock.release()


def scaffold_env_file(env_path: Path = ENV_PATH) -> bool:
    try:
        with _locked_env_path(env_path) as target:
            if _read_env_content(target):
                return False
            _atomic_write_env(target, ENV_TEMPLATE)
            _tighten_env_permissions(target)
    except OSError:
        return False
    return True


def read_env_values(env_path: Path) -> dict[str, str]:
    content = _read_env_content(env_path)
    return {
        key: value
        for key, value in dotenv_values(stream=StringIO(content)).items()
        if value is not None
    }


def _read_env_content(env_path: Path) -> str:
    """Only absence counts as empty; admit one regular, bounded descriptor."""
    try:
        target = env_path.resolve()
        try:
            fd = os.open(
                target,
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0),
            )
        except FileNotFoundError:
            return ""
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ENV_BYTES:
                raise OSError
            content = handle.read(MAX_ENV_BYTES + 1)
            if len(content) > MAX_ENV_BYTES:
                raise OSError
        return content.decode("utf-8")
    except (OSError, UnicodeError, RuntimeError):
        raise OSError(
            "Could not read configuration; expected a regular UTF-8 file of at most 64 KiB"
        ) from None


def upsert_env_value(env_path: Path, key: str, value: str) -> None:
    """Set ``key`` in the env file, keeping every other line.

    Only a missing file counts as empty. Any other read error is raised so an
    unreadable file is never replaced by one that holds just ``key``.
    """
    with _locked_env_path(env_path) as target:
        content = _read_env_content(target)
        _atomic_write_env(target, content, assignment=(key, value))
        _tighten_env_permissions(target)
