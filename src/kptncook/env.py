"""
Environment configuration helpers for kptncook.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_API_KEY = "6q7QNKy-oIgk-IMuWisJ-jfN7s6"
ENV_PATH = Path.home() / ".kptncook" / ".env"
ENV_TEMPLATE = f"""# kptncook configuration
#
# Required
KPTNCOOK_API_KEY={DEFAULT_API_KEY}
#
# Optional: access token for favorites
KPTNCOOK_ACCESS_TOKEN=
#
# Optional: password manager integration
# KPTNCOOK_USERNAME_COMMAND="op read op://Personal/KptnCook/username"
# KPTNCOOK_PASSWORD_COMMAND="op read op://Personal/KptnCook/password"
"""


def scaffold_env_file(env_path: Path = ENV_PATH) -> bool:
    try:
        if env_path.exists() and env_path.stat().st_size > 0:
            return False
    except OSError:
        return False
    env_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        env_path.write_text(ENV_TEMPLATE)
    except OSError:
        return False
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
    try:
        content = env_path.read_text()
        lines = content.splitlines()
    except OSError:
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
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(new_lines) + "\n")
