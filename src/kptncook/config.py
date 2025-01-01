"""
Base settings for kptncook.
"""

from pathlib import Path

from pydantic import AnyHttpUrl, DirectoryPath, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(Path.home() / ".kptncook" / ".env"), extra="ignore"
    )
    root: DirectoryPath = Field(Path.home() / ".kptncook", env="KPTNCOOK_HOME")  # type: ignore
    kptncook_api_key: str
    kptncook_access_token: str | None = None
    kptncook_api_url: AnyHttpUrl = AnyHttpUrl("https://mobile.kptncook.com")
    mealie_url: AnyHttpUrl = AnyHttpUrl("http://localhost:9000/api")

    mealie_username: str | None = None
    mealie_password: str | None = None
    mealie_api_token: str | None = None

    # Password manager integration
    kptncook_username_command: str | None = None
    kptncook_password_command: str | None = None

    @field_validator("root", mode="before")
    def root_must_exist(cls, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    @model_validator(mode="after")
    def check_mealie_auth(self):
        if (self.mealie_password is None or self.mealie_username is None) and self.mealie_api_token is None:
            raise ValueError("must specify either mealie_username/password or mealie_api_token")

        return self
