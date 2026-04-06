"""
Base settings for kptncook.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import AnyHttpUrl, DirectoryPath, Field, ValidationError, field_validator
from pydantic_core import PydanticUndefined
from pydantic_settings import BaseSettings, SettingsConfigDict
from rich import print as rprint

from .env import ENV_PATH, scaffold_env_file

_MISSING_DEFAULT = object()
_settings_cache: Settings | None = None


def _missing_required_fields(error: ValidationError) -> set[str]:
    missing: set[str] = set()
    for err in error.errors():
        loc = err.get("loc") or ()
        if not loc:
            continue
        field = str(loc[-1])
        err_type = err.get("type")
        if err_type == "missing":
            missing.add(field)
        elif field == "kptncook_api_key" and err_type == "value_error":
            missing.add(field)
    return missing


class SettingsError(Exception):
    """Raised when configuration is invalid at runtime."""

    def __init__(
        self,
        validation_error: ValidationError,
        *,
        missing_fields: set[str],
        env_path: Path,
        scaffolded: bool,
    ) -> None:
        super().__init__("Invalid kptncook configuration")
        self.validation_error = validation_error
        self.missing_fields = missing_fields
        self.env_path = env_path
        self.scaffolded = scaffolded


def render_settings_error(error: SettingsError) -> None:
    if "kptncook_api_key" in error.missing_fields:
        rprint("[red]Missing required configuration.[/red]")
        if error.scaffolded:
            rprint(f"Created {error.env_path} with a starter template.")
        rprint("Add your KptnCook API key to the .env file or your shell:")
        rprint(f"  {error.env_path}")
        rprint("  KPTNCOOK_API_KEY=your-api-key-here")
        rprint("Then re-run the command, or use `kptncook-setup` for guided setup.")
        rprint("See README.md for details.")
        return

    rprint("[red]Invalid configuration.[/red]")
    rprint(str(error.validation_error))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_PATH, extra="ignore")
    root: DirectoryPath = Field(
        Path.home() / ".kptncook", validation_alias="KPTNCOOK_HOME"
    )  # type: ignore
    kptncook_api_key: str
    kptncook_access_token: str | None = None
    kptncook_api_url: AnyHttpUrl = AnyHttpUrl("https://mobile.kptncook.com")
    kptncook_lang: str = "de"
    kptncook_store: str = "de"
    kptncook_preferences: str | None = None
    mealie_url: AnyHttpUrl = AnyHttpUrl("http://localhost:9000/api")
    mealie_username: str | None = None
    mealie_password: str | None = None
    mealie_api_token: str | None = None

    # Password manager integration
    kptncook_username_command: str | None = None
    kptncook_password_command: str | None = None
    kptncook_group_ingredients_by_typ: bool = False
    kptncook_ingredient_group_labels: str | None = None

    @field_validator("root", mode="before")
    def root_must_exist(cls, path: str | Path | os.PathLike[str]) -> Path:
        path = Path(os.path.expandvars(os.fspath(path))).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @field_validator("kptncook_api_key")
    def api_key_must_be_set(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("KPTNCOOK_API_KEY is required")
        return value


def get_settings() -> Settings:
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    try:
        loaded = Settings()  # type: ignore
    except ValidationError as exc:
        missing_fields = _missing_required_fields(exc)
        scaffolded = False
        if "kptncook_api_key" in missing_fields:
            scaffolded = scaffold_env_file(ENV_PATH)
        raise SettingsError(
            exc,
            missing_fields=missing_fields,
            env_path=ENV_PATH,
            scaffolded=scaffolded,
        ) from exc
    settings._apply_overrides(loaded)
    _settings_cache = loaded
    return loaded


def clear_settings_cache() -> None:
    global _settings_cache
    _settings_cache = None


class _LazySettingsProxy:
    def __init__(self) -> None:
        object.__setattr__(self, "_defaults", self._build_defaults())
        object.__setattr__(self, "_overrides", {})

    def _build_defaults(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for name, field in Settings.model_fields.items():
            default = field.default
            if default is PydanticUndefined:
                defaults[name] = _MISSING_DEFAULT
            else:
                defaults[name] = default
        return defaults

    def _apply_overrides(self, target: Settings) -> None:
        overrides = object.__getattribute__(self, "_overrides")
        for name, value in overrides.items():
            setattr(target, name, value)

    def __getattr__(self, name: str) -> Any:
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        if _settings_cache is not None:
            return getattr(_settings_cache, name)
        defaults = object.__getattribute__(self, "_defaults")
        if name in defaults:
            if defaults[name] is _MISSING_DEFAULT:
                raise AttributeError(name)
            return defaults[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        overrides = object.__getattribute__(self, "_overrides")
        defaults = object.__getattribute__(self, "_defaults")
        if name in defaults and defaults[name] == value and _settings_cache is None:
            overrides.pop(name, None)
        else:
            overrides[name] = value
        if _settings_cache is not None:
            setattr(_settings_cache, name, value)


settings = _LazySettingsProxy()
