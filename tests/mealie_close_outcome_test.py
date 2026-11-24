"""Cleanup cannot erase committed counts or primary transport failures."""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from typer.testing import CliRunner

from kptncook.cli import app
from kptncook.models import Recipe
from kptncook.services import workflows
from kptncook.services.repository import RepositoryRecipesResult


@pytest.mark.parametrize(
    "operation", ["success", "ambiguous-create", "inventory-failure", "interrupt"]
)
def test_close_failure_preserves_operation_outcome(
    monkeypatch, minimal, caplog, operation
):
    primary = httpx.ReadTimeout("synthetic transport failure")
    client = SimpleNamespace(
        create_recipe=Mock(),
        close=Mock(side_effect=RuntimeError("private-cleanup-detail")),
    )
    recipes = [
        Recipe.model_validate(minimal),
        Recipe.model_validate({**minimal, "_id": {"$oid": "0" * 24}}),
    ]
    if operation == "ambiguous-create":
        client.create_recipe.side_effect = [None, primary]
    monkeypatch.setattr(workflows, "get_mealie_client", lambda: client)
    inventory = Mock(return_value=[])
    if operation == "inventory-failure":
        inventory.side_effect = primary
    elif operation == "interrupt":
        inventory.side_effect = KeyboardInterrupt()
    monkeypatch.setattr(workflows, "get_kptncook_recipes_from_mealie", inventory)
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(recipes, []),
    )
    monkeypatch.setattr(workflows, "kptncook_to_mealie", lambda recipe: recipe)

    if operation == "inventory-failure":
        with pytest.raises(httpx.ReadTimeout) as caught:
            workflows.sync_with_mealie_result()
        assert caught.value is primary
    elif operation == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            workflows.sync_with_mealie_result()
    else:
        result = workflows.sync_with_mealie_result()
        assert result.created_count == (1 if operation == "ambiguous-create" else 2)
        assert result.cleanup_warning
        assert "No automatic replay" in result.cleanup_warning
        if operation == "ambiguous-create":
            [failure] = result.failed_recipes
            assert "outcome is unknown" in failure.reason
        else:
            assert result.failed_recipes == []
    client.close.assert_called_once_with()
    assert "cleanup failed" in caplog.text
    assert "private-cleanup-detail" not in caplog.text


def test_failed_warning_delivery_preserves_sync_receipt(monkeypatch, minimal):
    recipes = [
        Recipe.model_validate({**minimal, "_id": {"$oid": digit * 24}})
        for digit in "123"
    ]
    client = SimpleNamespace(
        create_recipe=Mock(), close=Mock(side_effect=OSError("private close detail"))
    )
    convert = Mock(
        side_effect=[recipes[0], ValueError("invalid conversion"), recipes[2]]
    )
    warning = Mock(side_effect=RuntimeError("warning handler failed"))
    monkeypatch.setattr(workflows, "get_mealie_client", lambda: client)
    monkeypatch.setattr(
        workflows, "get_kptncook_recipes_from_mealie", lambda client: []
    )
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(recipes, []),
    )
    monkeypatch.setattr(workflows, "kptncook_to_mealie", convert)
    monkeypatch.setattr(workflows.logger, "warning", warning)

    result = workflows.sync_with_mealie_result()

    assert result.created_count == 2
    assert [call.args[0] for call in client.create_recipe.call_args_list] == [
        recipes[0],
        recipes[2],
    ]
    [failure] = result.failed_recipes
    assert failure.recipe_id == recipes[1].id.oid
    assert failure.reason == "ValueError: invalid conversion"
    assert result.cleanup_warning
    assert "Mealie client cleanup failed (OSError)" in result.cleanup_warning
    assert "private close detail" not in result.cleanup_warning
    client.close.assert_called_once_with()
    assert warning.call_count == 2


def cleanup_result():
    return SimpleNamespace(
        created_count=2,
        invalid_repository_entries=[],
        failed_recipes=[],
        cleanup_warning="Mealie client cleanup failed. No automatic replay.",
    )


def test_cli_reports_committed_count_and_cleanup_warning(monkeypatch):
    from importlib import import_module

    cli = import_module("kptncook.cli")

    monkeypatch.setattr(cli, "sync_with_mealie_workflow", cleanup_result)
    result = CliRunner().invoke(app, ["sync-with-mealie"])
    assert result.exit_code == 1
    assert "Created 2 recipes" in result.output
    assert "cleanup failed" in result.output


def test_count_wrapper_does_not_hide_cleanup_warning(monkeypatch):
    monkeypatch.setattr(workflows, "sync_with_mealie_result", cleanup_result)
    with pytest.raises(
        workflows.UserFacingError, match="Created 2 recipes.*cleanup failed"
    ):
        workflows.sync_with_mealie()
