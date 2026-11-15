"""Deletion compares the exact previewed store records under writer ownership."""

from contextlib import contextmanager
from datetime import date
from importlib import import_module

import pytest
from typer.testing import CliRunner

from kptncook.repositories import RecipeInDb, RecipeRepository, RepositoryError
from kptncook.services import repository as service
from kptncook.services import workflows


def entry(minimal, oid):
    return RecipeInDb(date=date(2026, 11, 15), data={**minimal, "_id": {"$oid": oid}})


@pytest.mark.parametrize("change", ["title", "extension", "date"])
@pytest.mark.parametrize("selection", [["0", "1"], ["--oid", "a", "--oid", "b"]])
def test_confirmation_refuses_changed_selection_atomically(
    tmp_path, monkeypatch, minimal, change, selection
):
    cli = import_module("kptncook.cli")
    reader = RecipeRepository(tmp_path)
    writer = RecipeRepository(tmp_path)
    reader.add_list([entry(minimal, oid) for oid in ["a", "b", "unrelated"]])
    monkeypatch.setattr(service, "get_repository", lambda: reader)
    after_write = {}

    def confirm(_prompt):
        changed = writer.list_by_id()["b"]
        if change == "title":
            changed.data["localizedTitle"] = {"de": "Changed during confirmation"}
        elif change == "extension":
            # Unknown source metadata must survive even if Recipe ignores it.
            changed.data["new_metadata"] = "changed"
        else:
            changed.date = date(2026, 11, 16)
        writer.add(changed)
        after_write["primary"] = reader.path.read_bytes()
        after_write["backup"] = reader.backup_path.read_bytes()
        return True

    monkeypatch.setattr(cli.typer, "confirm", confirm)
    result = CliRunner().invoke(cli.app, ["delete-recipes", *selection])

    assert result.exit_code == 1
    assert "Reload" in result.output
    assert "no recipes were deleted" in result.output
    assert reader.path.read_bytes() == after_write["primary"]
    assert reader.backup_path.read_bytes() == after_write["backup"]


@pytest.mark.parametrize("change", ["unrelated", "selected-missing"])
def test_confirmation_allows_unrelated_changes_and_missing_selected_ids(
    tmp_path, monkeypatch, minimal, change
):
    cli = import_module("kptncook.cli")
    reader = RecipeRepository(tmp_path)
    writer = RecipeRepository(tmp_path)
    reader.add_list([entry(minimal, oid) for oid in ["a", "b", "keep"]])
    monkeypatch.setattr(service, "get_repository", lambda: reader)

    def confirm(_prompt):
        if change == "selected-missing":
            writer.delete_by_ids(["b"])
        else:
            changed = writer.list_by_id()["keep"]
            changed.data["new_metadata"] = "keep me"
            writer.add(changed)
        return True

    monkeypatch.setattr(cli.typer, "confirm", confirm)
    result = CliRunner().invoke(cli.app, ["delete-recipes", "--oid", "a", "--oid", "b"])

    assert result.exit_code == 0, result.output
    assert list(reader.list_by_id()) == ["keep"]
    if change == "unrelated":
        assert reader.list_by_id()["keep"].data["new_metadata"] == "keep me"
    else:
        assert "Some recipes were not found: b" in result.output


def test_repository_delete_checks_expected_records_inside_lock(
    tmp_path, minimal, monkeypatch
):
    reader, writer = RecipeRepository(tmp_path), RecipeRepository(tmp_path)
    reader.add_list([entry(minimal, oid) for oid in ["a", "b", "keep"]])
    expected = reader.list_by_id()
    changed = expected["b"].model_copy(deep=True)
    changed.data["extra"] = "changed"
    write_lock = reader._write_lock
    after_write = {}

    @contextmanager
    def concurrent_write_before_admission():
        # The other writer commits after deletion starts but before our lock is
        # acquired. Reading/comparing outside that lock would miss this edit.
        writer.add(changed)
        after_write["primary"] = reader.path.read_bytes()
        after_write["backup"] = reader.backup_path.read_bytes()
        with write_lock():
            yield

    monkeypatch.setattr(reader, "_write_lock", concurrent_write_before_admission)

    with pytest.raises(RepositoryError, match="Reload"):
        reader.delete_by_ids(["a", "b", "a"], expected=expected)

    assert reader.path.read_bytes() == after_write["primary"]
    assert reader.backup_path.read_bytes() == after_write["backup"]


def test_repository_delete_expected_missing_id_cannot_delete_new_record(
    tmp_path, minimal
):
    reader, writer = RecipeRepository(tmp_path), RecipeRepository(tmp_path)
    writer.add(entry(minimal, "new"))
    with pytest.raises(RepositoryError, match="Reload"):
        reader.delete_by_ids(["new"], expected={})
    assert list(reader.list_by_id()) == ["new"]


def test_selection_uses_one_store_read(tmp_path, monkeypatch, minimal, mocker):
    reader = RecipeRepository(tmp_path)
    stored = entry(minimal, "a")
    reader.add(stored)
    monkeypatch.setattr(service, "get_repository", lambda: reader)
    read = mocker.spy(reader, "_read_models")

    result = workflows.delete_recipes_by_selection(indices=[0], oids=[])

    assert read.call_count == 1
    assert result.expected == {"a": stored}
    assert result.recipes[0].id.oid == "a"


def test_force_still_checks_the_selected_record(tmp_path, monkeypatch, minimal):
    reader, writer = RecipeRepository(tmp_path), RecipeRepository(tmp_path)
    reader.add(entry(minimal, "a"))
    monkeypatch.setattr(service, "get_repository", lambda: reader)
    read_selection = workflows.delete_recipes_by_selection

    def select_then_edit(**kwargs):
        result = read_selection(**kwargs)
        changed = writer.list_by_id()["a"]
        changed.data["extra"] = "changed after preview"
        writer.add(changed)
        return result

    cli = import_module("kptncook.cli")
    monkeypatch.setattr(cli, "delete_recipes_by_selection", select_then_edit)
    result = CliRunner().invoke(cli.app, ["delete-recipes", "0", "--force"])

    assert result.exit_code == 1
    assert "Reload" in result.output
    assert list(reader.list_by_id()) == ["a"]
