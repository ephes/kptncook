import pydantic
import pytest

from tests.conftest import MockSettings


def test_config_accepts_mealie_username_password():
    MockSettings(
        mealie_username="test", mealie_password="password", kptncook_api_key="test"
    )


def test_config_accepts_mealie_token():
    MockSettings(mealie_api_token="test", kptncook_api_key="test")


def test_config_rejects_empty_mealie_auth():
    with pytest.raises(pydantic.ValidationError) as exception_info:
        MockSettings(kptncook_api_key="test")

    assert "must specify either" in str(exception_info.value)
