from kptncook.env import read_env_values, upsert_env_value


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
