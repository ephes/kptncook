import subprocess
import sys
from datetime import date
from pathlib import Path
from importlib import import_module

import httpx
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import kptncook
from kptncook.config import Settings, SettingsError
from kptncook.models import Recipe
from kptncook.repositories import RecipeInDb
from kptncook.services.repository import InvalidStoredRecipe, RepositoryRecipesResult
from kptncook.services.workflows import (
    MealieSyncFailure,
    MealieSyncIssue,
    SyncWithMealieResult,
)


runner = CliRunner()


def test_help_command_outputs_usage(capsys):
    kptncook.help_command(command=None, all_commands=False)

    output = capsys.readouterr().out
    assert "Usage:" in output


def test_help_command_all_outputs_usage(capsys):
    kptncook.help_command(command=None, all_commands=True)

    output = capsys.readouterr().out
    assert "Usage:" in output


def test_help_command_unknown_command_exits():
    with pytest.raises(SystemExit):
        kptncook.help_command(command="not-a-command", all_commands=False)


def test_ls_alias_calls_list_recipes(monkeypatch):
    cli_module = import_module("kptncook.cli")
    called = {"value": False}

    def fake_list_recipes():
        called["value"] = True

    monkeypatch.setattr(cli_module, "list_recipes", fake_list_recipes)

    kptncook.list_recipes_alias()

    assert called["value"] is True


def test_search_by_id_share_url_handles_http_error(monkeypatch):
    def fake_get(_url, **_kwargs):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(kptncook.httpx, "get", fake_get)

    with pytest.raises(SystemExit):
        kptncook.search_kptncook_recipe_by_id(
            "https://share.kptncook.com/Dh4a/351k4802"
        )


def test_command_renders_config_errors_at_cli_boundary(monkeypatch, capsys):
    cli_module = import_module("kptncook.cli")

    with pytest.raises(ValidationError) as exc_info:
        Settings(kptncook_api_key="   ")

    def fail_with_config_error():
        raise SettingsError(
            exc_info.value,
            missing_fields={"kptncook_api_key"},
            env_path=Path("/tmp/.kptncook/.env"),
            scaffolded=False,
        )

    monkeypatch.setattr(cli_module, "get_today_recipes", fail_with_config_error)

    with pytest.raises(SystemExit):
        cli_module.list_kptncook_today()

    output = capsys.readouterr().out
    assert "Missing required configuration." in output
    assert "KPTNCOOK_API_KEY=your-api-key-here" in output


def test_list_recipes_renders_invalid_repository_warning(monkeypatch, capsys, minimal):
    cli_module = import_module("kptncook.cli")
    recipe = Recipe.model_validate(minimal)

    monkeypatch.setattr(
        cli_module,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(
            recipes=[recipe],
            invalid_entries=[
                InvalidStoredRecipe(
                    position=2,
                    recipe_id="broken",
                    reason="steps: Field required",
                )
            ],
        ),
    )

    cli_module.list_recipes()

    output = capsys.readouterr().out
    assert "Warning:" in output
    assert "skipped 1 invalid stored recipe" in output
    assert "- broken: steps: Field required" in output
    assert "Minimal Recipe" in output


def test_sync_with_mealie_command_smoke_renders_warning_summary(monkeypatch):
    cli_module = import_module("kptncook.cli")

    monkeypatch.setattr(
        cli_module,
        "sync_with_mealie_workflow",
        lambda: SyncWithMealieResult(
            created_count=2,
            invalid_repository_entries=[
                InvalidStoredRecipe(
                    position=2,
                    recipe_id="broken",
                    reason="steps: Field required",
                )
            ],
        ),
    )

    result = runner.invoke(cli_module.app, ["sync-with-mealie"])

    assert result.exit_code == 0
    assert "Warning:" in result.output
    assert "skipped 1 invalid stored recipe" in result.output
    assert "- broken: steps: Field required" in result.output
    assert "Created 2 recipes" in result.output


@pytest.mark.parametrize("command", ["sync-with-mealie", "sync"])
def test_sync_commands_report_failures_after_created_count(monkeypatch, command):
    cli_module = import_module("kptncook.cli")
    monkeypatch.setattr(cli_module, "save_todays_recipes_workflow", lambda: 0)
    monkeypatch.setattr(
        cli_module,
        "sync_with_mealie_workflow",
        lambda: SyncWithMealieResult(
            created_count=1,
            invalid_repository_entries=[
                InvalidStoredRecipe(2, "broken", "steps: Field required")
            ],
            failed_recipes=[
                MealieSyncFailure(
                    recipe_id="recipe-1",
                    recipe_name="Soup [red]",
                    reason="HTTP 409: Recipe already exists",
                ),
                MealieSyncFailure(
                    recipe_id="recipe-2",
                    recipe_name="Salad",
                    reason="ReadTimeout: outcome unknown. The next sync checks stored identity.",
                ),
            ],
        ),
    )

    result = runner.invoke(cli_module.app, [command])

    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert "Created 1 recipes" in output
    assert "Warning:" in output
    assert "broken: steps: Field required" in output
    assert "Failed to sync 2 recipes" in output
    assert "Soup [red]" in output
    assert "recipe-1" in output
    assert "Recipe already exists" in output
    assert "Salad" in output
    assert "recipe-2" in output
    assert "next sync checks stored identity" in output
    assert output.index("Created 1 recipes") < output.index("Failed to sync")


def test_sync_with_mealie_command_reports_skipped_and_failed(monkeypatch):
    cli_module = import_module("kptncook.cli")

    monkeypatch.setattr(
        cli_module,
        "sync_with_mealie_workflow",
        lambda: SyncWithMealieResult(
            created_count=1,
            invalid_repository_entries=[],
            skipped_existing=[
                MealieSyncIssue(name="Pasta [vegan]", reason="name already used")
            ],
            failed=[
                MealieSyncIssue(
                    name="Soup", reason="Request failed: connection refused"
                )
            ],
        ),
    )

    result = runner.invoke(cli_module.app, ["sync-with-mealie"])

    assert result.exit_code == 1
    assert "Created 1 recipes" in result.output
    assert "Skipped 1 recipes" in result.output
    assert "- Pasta [vegan]: name already used" in result.output
    assert "Failed to create 1 recipes" in result.output
    assert "- Soup: Request failed: connection refused" in result.output


def test_sync_with_mealie_command_reports_legacy_skips_as_unsynced(monkeypatch):
    cli_module = import_module("kptncook.cli")

    monkeypatch.setattr(
        cli_module,
        "sync_with_mealie_workflow",
        lambda: SyncWithMealieResult(
            created_count=0,
            invalid_repository_entries=[],
            skipped_existing=[MealieSyncIssue(name="Pasta", reason="clash")],
        ),
    )

    result = runner.invoke(cli_module.app, ["sync-with-mealie"])

    assert result.exit_code == 1
    assert "- Pasta: clash" in result.output


def test_sync_command_does_not_double_report_compatibility_failures(monkeypatch):
    cli_module = import_module("kptncook.cli")
    monkeypatch.setattr(
        cli_module,
        "sync_with_mealie_workflow",
        lambda: SyncWithMealieResult(
            created_count=0,
            invalid_repository_entries=[],
            failed=[MealieSyncIssue("Soup [red]", "bad [bold]")],
            failed_recipes=[MealieSyncFailure("recipe-1", "Soup [red]", "bad [bold]")],
        ),
    )

    result = runner.invoke(cli_module.app, ["sync-with-mealie"])

    assert result.exit_code == 1
    assert result.output.count("Soup [red]") == 1
    assert "recipe-1" in result.output
    assert "bad [bold]" in result.output


def test_access_token_command_saves_token_without_printing_it(monkeypatch, tmp_path):
    cli_module = import_module("kptncook.cli")
    env_path = tmp_path / ".env"
    captured = {}

    monkeypatch.setattr(
        cli_module,
        "get_kptncook_access_token_workflow",
        lambda: "secret-token-123",
    )

    def fake_upsert_env_value(path, key, value):
        captured["path"] = path
        captured["key"] = key
        captured["value"] = value

    monkeypatch.setattr(cli_module, "ENV_PATH", env_path)
    monkeypatch.setattr(cli_module, "upsert_env_value", fake_upsert_env_value)

    result = runner.invoke(cli_module.app, ["kptncook-access-token"])

    assert result.exit_code == 0
    assert captured == {
        "path": env_path,
        "key": "KPTNCOOK_ACCESS_TOKEN",
        "value": "secret-token-123",
    }
    assert "Access token retrieved successfully" in result.output
    assert "Saved KPTNCOOK_ACCESS_TOKEN to" in result.output
    assert str(env_path) in result.output.replace("\n", "")
    assert "secret-token-123" not in result.output


def test_setup_is_registered_as_subcommand():
    cli_module = import_module("kptncook.cli")

    result = runner.invoke(cli_module.app, ["setup", "--help"])

    assert result.exit_code == 0
    assert "Interactive setup for kptncook configuration." in result.output


def test_kptncook_setup_entrypoint_imports_without_cycle():
    # The standalone `kptncook-setup` console script imports `kptncook_setup`
    # before `kptncook`, which previously triggered a circular import. Use a
    # fresh interpreter so the import order matches the entry point (in-process
    # tests already have `kptncook` imported and cannot reproduce it).
    result = subprocess.run(
        [sys.executable, "-c", "import kptncook_setup; assert kptncook_setup.setup"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_discovery_list_save_command_smoke_uses_service_result(monkeypatch, minimal):
    cli_module = import_module("kptncook.cli")
    recipe = RecipeInDb(date=date.today(), data=minimal)
    captured = {}

    monkeypatch.setattr(
        cli_module,
        "get_discovery_list_recipes",
        lambda *, list_type, list_id: [recipe],
    )

    def fake_save_recipe_entries(recipes):
        captured["recipes"] = recipes
        return len(recipes)

    monkeypatch.setattr(cli_module, "save_recipe_entries", fake_save_recipe_entries)

    result = runner.invoke(
        cli_module.app,
        ["discovery-list", "--list-type", "latest", "--save"],
    )

    assert result.exit_code == 0
    assert captured["recipes"] == [recipe]
    assert "Added 1 recipes to local repository" in result.output
