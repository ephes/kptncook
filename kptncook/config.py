"""
Base settings for kptncook.
"""
import sys
from pathlib import Path

from pydantic import (
    AnyHttpUrl,
    BaseSettings,
    DirectoryPath,
    Field,
    ValidationError,
    validator,
)
from rich import print as rprint

ROOT_DIR = Path(__file__).resolve(strict=True).parent.parent


class Settings(BaseSettings):
    root: DirectoryPath = Field(Path.home() / ".kptncook", env="KPTNCOOK_HOME")
    kptncook_api_key: str = Field(..., env="KPTNCOOK_API_KEY")
    kptncook_access_token: str = Field(None, env="KPTNCOOK_ACCESS_TOKEN")
    kptncook_api_url: AnyHttpUrl = Field(
        "https://mobile.kptncook.com", env="KPTNCOOK_API_URL"
    )
    mealie_url: AnyHttpUrl = Field("http://localhost:9000/api", env="MEALIE_URL")
    mealie_username: str = Field(..., env="MEALIE_USERNAME")
    mealie_password: str = Field(..., env="MEALIE_PASSWORD")

    @validator("root", pre=True)
    def root_must_exist(cls, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    class Config:
        env_file = Path.home() / ".kptncook" / ".env"


try:
    settings = Settings()  # type: ignore
except ValidationError as e:
    rprint("validation error: ", e)
    sys.exit(1)
