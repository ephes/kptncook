"""
Base settings for kptncook.
"""

import sys
from pathlib import Path

from pydantic import AnyHttpUrl, DirectoryPath, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from rich import print as rprint


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(Path.home() / ".kptncook" / ".env"), extra="ignore"
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
    mealie_url: AnyHttpUrl = AnyHttpUrl("http://localhost:9000/api")
    mealie_username: str
    mealie_password: str

    # Password manager integration
    kptncook_username_command: str | None = None
    kptncook_password_command: str | None = None
    kptncook_group_ingredients_by_typ: bool = False
    kptncook_ingredient_group_labels: str | None = None

    @field_validator("root", mode="before")
    def root_must_exist(cls, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path


try:
    settings = Settings()  # type: ignore
except ValidationError as e:
    rprint("validation error: ", e)
    sys.exit(1)
