"""
Environment configuration helpers for kptncook.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

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


def _atomic_write_env(env_path: Path, content: str) -> None:
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
        with os.fdopen(fd, "w") as handle:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), ENV_FILE_MODE)
            handle.write(content)
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


def scaffold_env_file(env_path: Path = ENV_PATH) -> bool:
    try:
        if env_path.exists() and env_path.stat().st_size > 0:
            return False
    except OSError:
        return False
    try:
        _atomic_write_env(env_path, ENV_TEMPLATE)
    except OSError:
        return False
    _tighten_env_permissions(env_path)
    return True


def read_env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        content = env_path.read_text()
    except OSError:
        return values
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def upsert_env_value(env_path: Path, key: str, value: str) -> None:
    """Set ``key`` in the env file, keeping every other line.

    Only a missing file counts as empty. Any other read error is raised so an
    unreadable file is never replaced by one that holds just ``key``.
    """
    try:
        content = env_path.read_text()
        lines = content.splitlines()
    except FileNotFoundError:
        lines = []
    updated = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    _atomic_write_env(env_path, "\n".join(new_lines) + "\n")
    _tighten_env_permissions(env_path)
