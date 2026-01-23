"""
Base settings for kptncook.
"""

import os
import sys
from pathlib import Path

from pydantic import AnyHttpUrl, DirectoryPath, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from rich import print as rprint

from .env import ENV_PATH, scaffold_env_file


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


def _render_missing_settings_message(
    missing_fields: set[str],
    env_path: Path,
    scaffolded: bool,
) -> None:
    if "kptncook_api_key" not in missing_fields:
        return
    rprint("[red]Missing required configuration.[/red]")
    if scaffolded:
        rprint(f"Created {env_path} with a starter template.")
    rprint("Add your KptnCook API key to the .env file or your shell:")
    rprint(f"  {env_path}")
    rprint("  KPTNCOOK_API_KEY=your-api-key-here")
    rprint("See README.md for details.")


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


try:
    settings = Settings()  # type: ignore
except ValidationError as e:
    missing_fields = _missing_required_fields(e)
    scaffolded = False
    if "kptncook_api_key" in missing_fields:
        scaffolded = scaffold_env_file(ENV_PATH)
        _render_missing_settings_message(missing_fields, ENV_PATH, scaffolded)
    else:
        rprint("validation error: ", e)
    sys.exit(1)
