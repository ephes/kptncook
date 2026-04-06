import os
import stat

from kptncook.env import ENV_TEMPLATE as MAIN_ENV_TEMPLATE
from kptncook.env import read_env_values, scaffold_env_file, upsert_env_value
from kptncook_setup import ENV_TEMPLATE as SETUP_ENV_TEMPLATE


def test_read_env_values_skips_comments(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment\nKPTNCOOK_API_KEY=abc123\n\nKPTNCOOK_ACCESS_TOKEN=token\n"
    )

    values = read_env_values(env_path)

    assert values["KPTNCOOK_API_KEY"] == "abc123"
    assert values["KPTNCOOK_ACCESS_TOKEN"] == "token"


def test_upsert_env_value_updates_existing(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("KPTNCOOK_API_KEY=old\n")

    upsert_env_value(env_path, "KPTNCOOK_API_KEY", "new")

    assert env_path.read_text() == "KPTNCOOK_API_KEY=new\n"


def test_upsert_env_value_appends_missing(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("KPTNCOOK_API_KEY=abc\n")

    upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", "token")

    assert (
        env_path.read_text() == "KPTNCOOK_API_KEY=abc\n\nKPTNCOOK_ACCESS_TOKEN=token\n"
    )


def test_scaffold_env_file_sets_owner_only_permissions(tmp_path):
    env_path = tmp_path / ".env"

    assert scaffold_env_file(env_path) is True

    if os.name != "nt":
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_upsert_env_value_tightens_permissions(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("KPTNCOOK_API_KEY=abc\n")
    if os.name != "nt":
        env_path.chmod(0o644)

    upsert_env_value(env_path, "KPTNCOOK_ACCESS_TOKEN", "token")

    if os.name != "nt":
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_env_templates_are_kept_in_sync():
    assert MAIN_ENV_TEMPLATE == SETUP_ENV_TEMPLATE
