"""
Base settings for kptncook.
"""
from pathlib import Path

from pydantic import AnyHttpUrl, BaseSettings, DirectoryPath, Field, validator

ROOT_DIR = Path(__file__).resolve(strict=True).parent.parent


class Settings(BaseSettings):
    root: DirectoryPath = Field(Path.home() / ".kptncook", env="KPTNCOOK_HOME")
    api_key: str = Field(..., env="KPTNCOOK_API_KEY")
    mealie_url: AnyHttpUrl = Field("http://localhost:9000/api", env="MEALIE_URL")
    mealie_username: str = Field(..., env="MEALIE_USERNAME")
    mealie_password: str = Field(..., env="MEALIE_PASSWORD")

    @validator("root", pre=True)
    def root_must_exist(cls, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    class Config:
        env_file = ROOT_DIR / ".env"


settings = Settings()  # type: ignore
