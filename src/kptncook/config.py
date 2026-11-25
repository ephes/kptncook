"""
Base settings for kptncook.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from threading import Lock
from typing import Any, cast

from pydantic import AnyHttpUrl, DirectoryPath, Field, ValidationError, field_validator
from pydantic_core import PydanticUndefined
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import ENV_FILE_SENTINEL, DotenvType
from pydantic_settings.sources.utils import parse_env_vars
from rich import print as rprint

from .env import ENV_PATH, ENV_READ_ERROR, read_dotenv, scaffold_env_file

_MISSING_DEFAULT = object()
_dotenv_files: ContextVar[DotenvType | None] = ContextVar("dotenv_files", default=None)
_settings_cache: Settings | None = None
_settings_generation = 0
_settings_lock = Lock()
_SAFE_VALIDATION_MESSAGES = {
    "missing": "Required setting is missing",
    "string_type": "Expected text",
    "url_parsing": "Expected a valid HTTP(S) URL",
    "url_type": "Expected a URL string",
    "url_scheme": "Expected an HTTP(S) URL",
    "bool_parsing": "Expected a boolean",
    "float_parsing": "Expected a number",
    "finite_number": "Expected a finite number",
    "greater_than": "Number is below the allowed range",
    "less_than_equal": "Number exceeds the allowed range",
    "ingredient_group_label_conflict": "Conflicting labels for the same ingredient group",
}


def _safe_validation_diagnostics(error: ValidationError) -> tuple[str, ...]:
    # Locations, messages and context can all contain rejected credentials.
    # Only known top-level setting names and our own text may reach output.
    fields = {name: name.upper() for name in Settings.model_fields}
    fields["KPTNCOOK_HOME"] = "KPTNCOOK_HOME"
    fields["root"] = "KPTNCOOK_HOME"
    diagnostics = []
    errors = error.errors(include_input=False, include_context=False, include_url=False)
    for err in errors[:16]:
        loc = err.get("loc") or ()
        field = (
            fields.get(loc[0], "configuration")
            if loc and isinstance(loc[0], str)
            else "configuration"
        )
        code = err["type"]
        if code not in _SAFE_VALIDATION_MESSAGES:
            code = "invalid_value"
        message = _SAFE_VALIDATION_MESSAGES.get(
            code, "Invalid setting; check its format"
        )
        diagnostics.append(f"{field}: {message} ({code})")
    if len(errors) > 16:
        diagnostics.append(
            "Additional invalid settings omitted; correct these and retry."
        )
    return tuple(diagnostics)


def _missing_required_fields(error: ValidationError) -> set[str]:
    missing: set[str] = set()
    for err in error.errors(
        include_input=False, include_context=False, include_url=False
    ):
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
        validation_error: ValidationError | None,
        *,
        missing_fields: set[str],
        env_path: Path,
        scaffolded: bool,
    ) -> None:
        super().__init__("Invalid kptncook configuration")
        self.diagnostics = (
            _safe_validation_diagnostics(validation_error)
            if validation_error is not None
            else (ENV_READ_ERROR,)
        )
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
        rprint("Then re-run the command, or use `kptncook setup` for guided setup.")
        rprint("See README.md for details.")
        return

    rprint("[red]Invalid configuration.[/red]")
    for diagnostic in error.diagnostics:
        rprint(diagnostic)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_PATH, extra="ignore", hide_input_in_errors=True
    )
    root: DirectoryPath = Field(
        Path.home() / ".kptncook", validation_alias="KPTNCOOK_HOME"
    )  # type: ignore
    kptncook_api_key: str
    kptncook_access_token: str | None = None
    kptncook_api_url: AnyHttpUrl = AnyHttpUrl("https://mobile.kptncook.com")
    kptncook_lang: str = "de"
    kptncook_store: str = "de"
    kptncook_preferences: str | None = None
    mealie_inventory_timeout_seconds: float = Field(
        120.0, gt=0, le=86_400, allow_inf_nan=False
    )
    mealie_url: AnyHttpUrl = AnyHttpUrl("http://localhost:9000/api")
    mealie_username: str | None = None
    mealie_password: str | None = None
    mealie_api_token: str | None = None

    # Password manager integration
    kptncook_username_command: str | None = None
    kptncook_password_command: str | None = None
    kptncook_group_ingredients_by_typ: bool = False
    kptncook_ingredient_group_labels: str | None = None
    # Extra image hosts (comma-separated) allowed to receive the API key, in
    # addition to KptnCook's own image CDN.
    kptncook_image_hosts: str | None = None

    def __init__(
        self, *, _env_file: DotenvType | None = ENV_FILE_SENTINEL, **values: Any
    ) -> None:
        files = (
            type(self).model_config.get("env_file")
            if _env_file == ENV_FILE_SENTINEL
            else _env_file
        )
        token = _dotenv_files.set(files)
        try:
            # The built-in source opens its files before settings_customise_sources.
            super().__init__(_env_file=None, **values)
        finally:
            _dotenv_files.reset(token)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        source = cast(DotEnvSettingsSource, dotenv_settings)
        files = _dotenv_files.get()
        if isinstance(files, (str, os.PathLike)):
            files = (files,)
        variables: dict[str, str | None] = {}
        for file in files or ():
            path = Path(file)
            try:
                content = read_dotenv(path.expanduser())
            except (OSError, RuntimeError):
                pass
            else:
                variables.update(
                    parse_env_vars(
                        content,
                        case_sensitive=source.case_sensitive,
                        ignore_empty=source.env_ignore_empty,
                        parse_none_str=source.env_parse_none_str,
                    )
                )
                continue
            raise SettingsError(
                None, missing_fields=set(), env_path=path, scaffolded=False
            ) from None
        source.env_vars = variables
        return init_settings, env_settings, source, file_secret_settings

    @field_validator("root", mode="before")
    def root_must_exist(cls, path: str | Path | os.PathLike[str]) -> Path:
        path = Path(os.path.expandvars(os.fspath(path))).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @field_validator("kptncook_image_hosts")
    def image_hosts_must_be_host_names(cls, value: str | None) -> str | None:
        from .image_origin import parse_image_hosts

        parse_image_hosts(value)
        return value

    @field_validator("kptncook_ingredient_group_labels")
    def ingredient_group_labels_must_agree(cls, value: str | None) -> str | None:
        from .ingredient_groups import parse_ingredient_group_labels

        parse_ingredient_group_labels(value)
        return value

    @field_validator("kptncook_api_key")
    def api_key_must_be_set(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("KPTNCOOK_API_KEY is required")
        return value

    @field_validator("kptncook_access_token", mode="before")
    @classmethod
    def empty_optional_token_is_unset(cls, value: object) -> object:
        # The starter template contains an intentionally blank optional token.
        return None if value == "" else value


def _load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        # Keep only value-free data from the raw error. Anything raised while
        # the handler is active (including scaffold I/O failures) would retain
        # the raw validation error, with its rejected inputs, as context.
        error = SettingsError(
            exc,
            missing_fields=_missing_required_fields(exc),
            env_path=ENV_PATH,
            scaffolded=False,
        )
    if "kptncook_api_key" in error.missing_fields:
        error.scaffolded = scaffold_env_file(ENV_PATH)
    raise error from None


def get_settings() -> Settings:
    global _settings_cache
    while True:
        with _settings_lock:
            if _settings_cache is not None:
                return _settings_cache
            generation = _settings_generation

        try:
            loaded = _load_settings()
        except SettingsError:
            with _settings_lock:
                if _settings_cache is not None:
                    return _settings_cache
                if generation != _settings_generation:
                    continue
            raise

        with _settings_lock:
            # Reuse a completed load before deciding whether to build again.
            if _settings_cache is not None:
                return _settings_cache
            if generation != _settings_generation:
                continue
            settings._apply_overrides(loaded)
            _settings_cache = loaded
            return loaded


def clear_settings_cache() -> None:
    global _settings_cache, _settings_generation
    with _settings_lock:
        _settings_cache = None
        _settings_generation += 1


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
        with _settings_lock:
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
        with _settings_lock:
            if name in defaults and defaults[name] == value and _settings_cache is None:
                overrides.pop(name, None)
            else:
                overrides[name] = value
            if _settings_cache is not None:
                setattr(_settings_cache, name, value)


settings = _LazySettingsProxy()
