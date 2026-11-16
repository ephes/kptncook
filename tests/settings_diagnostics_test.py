"""Configuration diagnostics must never format rejected credential values."""

import traceback
from importlib import import_module

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from kptncook import config

SENTINEL = "synthetic-secret-r109"
BAD_URL = f"https://user:{SENTINEL}@example.test:invalid/api?token={SENTINEL}"


def test_cli_settings_diagnostics_omit_url_credentials(monkeypatch):
    monkeypatch.setenv("MEALIE_URL", BAD_URL)
    result = CliRunner().invoke(import_module("kptncook.cli").app, ["list-recipes"])
    assert result.exit_code == 1
    assert SENTINEL not in result.output
    assert "Invalid configuration" in result.output
    assert "MEALIE_URL" in result.output
    assert "url_parsing" in result.output


@pytest.mark.parametrize(
    "values, field",
    [
        ({"mealie_url": BAD_URL}, "MEALIE_URL"),
        ({"mealie_api_token": {SENTINEL: [SENTINEL]}}, "MEALIE_API_TOKEN"),
    ],
)
def test_direct_settings_validation_hides_rejected_inputs(values, field):
    with pytest.raises(ValidationError) as caught:
        config.Settings(**values)
    assert field.lower() in str(caught.value)
    assert SENTINEL not in str(caught.value)
    assert SENTINEL not in "".join(traceback.format_exception(caught.value))


def test_settings_error_chain_and_diagnostics_are_value_free(monkeypatch):
    monkeypatch.setenv("MEALIE_URL", BAD_URL)
    with pytest.raises(config.SettingsError) as caught:
        config.get_settings()
    error = caught.value
    assert SENTINEL not in "".join(traceback.format_exception(error))
    assert error.__cause__ is None
    assert error.__context__ is None
    assert SENTINEL not in repr(vars(error))


def test_diagnostics_bound_untrusted_locations_codes_and_context(capsys, tmp_path):
    raw = ValidationError.from_exception_data(
        "Settings",
        [
            {
                "type": "value_error",
                "loc": (SENTINEL * 100, SENTINEL),
                "input": {SENTINEL: SENTINEL},
                "ctx": {"error": ValueError(SENTINEL)},
            }
            for _ in range(100)
        ],
    )
    error = config.SettingsError(
        raw, missing_fields=set(), env_path=tmp_path / ".env", scaffolded=False
    )
    config.render_settings_error(error)
    output = capsys.readouterr().out
    assert SENTINEL not in output
    assert len(output) < 2000
    assert "invalid_value" in output


def test_missing_api_key_keeps_setup_guidance(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("KPTNCOOK_API_KEY")
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    with pytest.raises(config.SettingsError) as caught:
        config.get_settings()
    config.render_settings_error(caught.value)
    output = capsys.readouterr().out
    assert "KPTNCOOK_API_KEY=your-api-key-here" in output
    assert "kptncook setup" in output


def test_scaffold_publication_failure_does_not_retain_validation_error(
    monkeypatch, tmp_path
):
    from kptncook import env

    monkeypatch.delenv("KPTNCOOK_API_KEY")
    monkeypatch.setenv("MEALIE_URL", BAD_URL)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")

    def fail_sync(directory):
        raise OSError("synthetic directory sync failure")

    monkeypatch.setattr(env, "fsync_directory", fail_sync)
    with pytest.raises(env.ConfigurationPublishedError) as caught:
        config.get_settings()
    chained = []
    current: BaseException | None = caught.value
    while current is not None:
        chained.append(current)
        current = current.__cause__ or current.__context__
    assert not any(isinstance(item, ValidationError) for item in chained)
    assert SENTINEL not in "".join(traceback.format_exception(caught.value))
    frame_locals = []
    tb = caught.value.__traceback__
    while tb is not None:
        frame_locals.extend(tb.tb_frame.f_locals.values())
        tb = tb.tb_next
    assert not any(isinstance(value, ValidationError) for value in frame_locals)
