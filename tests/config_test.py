import pydantic
import pytest

from kptncook.config import Settings
from tests.conftest import temp_env


def test_config_accepts_mealie_username_password():
    with temp_env(MEALIE_USERNAME="test", MEALIE_PASSWORD="test", KPTNCOOK_API_KEY="test"):
        settings = Settings()


def test_config_accepts_mealie_token():
    with temp_env(MEALIE_API_TOKEN="test", KPTNCOOK_API_KEY="test"):
        settings = Settings()

def test_config_rejects_empty_mealie_auth():
    with pytest.raises(pydantic.ValidationError) as exception_info:
        with temp_env(KPTNCOOK_API_KEY="test"):
            settings = Settings()

    assert "mealie" in str(exception_info.value)