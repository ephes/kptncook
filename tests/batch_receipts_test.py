"""Offline failures retain exact receipts for already published recipes."""

import importlib
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kptncook.config import get_settings
from kptncook.http_errors import UserFacingError
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.models import Recipe
from kptncook.tandoor import TandoorExporter


@pytest.fixture
def recipes(minimal):
    return [
        Recipe.model_validate(
            dict(
                minimal,
                _id={"$oid": f"synthetic-{i}"},
                localizedTitle={"de": f"Recipe {i}"},
            )
        )
        for i in range(3)
    ]


@pytest.mark.parametrize("uncertain", [False, True])
def test_tandoor_batch_receipts_and_cli(recipes, tmp_path, monkeypatch, uncertain):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TandoorExporter, "get_cover_image_bytes", lambda *a, **kw: None)
    link = os.link

    def publish(src, dst):
        if Path(dst).name == "Recipe_1.zip" and not uncertain:
            raise OSError("synthetic failure")
        link(src, dst)

    def sync(directory):
        if uncertain and (tmp_path / "Recipe_1.zip").exists():
            raise OSError("synthetic sync failure")

    monkeypatch.setattr(os, "link", publish)
    monkeypatch.setattr("kptncook.exporter_utils.fsync_directory", sync)
    cli = importlib.import_module("kptncook.cli")
    errors = []

    def run(recipe_id):
        try:
            TandoorExporter().export(recipes)
        except UserFacingError as exc:
            errors.append(exc)
            raise

    monkeypatch.setattr(cli, "export_recipes_to_tandoor_workflow", run)
    result = CliRunner().invoke(cli.app, ["export-recipes-to-tandoor"])
    assert result.exit_code == 1
    assert result.output.count("Recipe_0.zip") == 1
    [error] = errors
    assert error.completed_paths == (tmp_path / "Recipe_0.zip",)
    assert error.failed_recipe_id == "synthetic-1"
    assert error.remaining_count == 1
    assert error.uncertain_paths == ((tmp_path / "Recipe_1.zip",) if uncertain else ())
    assert (tmp_path / "Recipe_0.zip").exists()
    assert not (tmp_path / "Recipe_2.zip").exists()
    assert not list(tmp_path.glob(".kptncook-*"))


@pytest.mark.parametrize(
    "failure",
    ["render", "asset", "note", "asset-sync", "note-sync", "asset-parent-sync"],
)
def test_markdown_batch_receipts(recipes, monkeypatch, failure):
    exporter = MarkdownExporter()
    monkeypatch.setattr(
        exporter, "_download_recipe_image", lambda recipe, **kw: b"synthetic"
    )
    out = get_settings().root / "export_md"
    render = exporter.render_recipe
    current = []

    def rendering(recipe, image_path=None):
        current.append(recipe.id.oid)
        if recipe is recipes[1] and failure == "render":
            raise ValueError("synthetic render failure")
        return render(recipe, image_path)

    monkeypatch.setattr(exporter, "render_recipe", rendering)
    link = os.link
    asset_targets = []

    def publish(src, dst):
        dst = Path(dst)
        second_asset = dst.suffix == ".jpg" and len(asset_targets) == 1
        second_note = dst.name == "Recipe 1.md"
        if (second_asset and failure == "asset") or (second_note and failure == "note"):
            raise OSError("synthetic publication failure")
        link(src, dst)
        if dst.suffix == ".jpg":
            asset_targets.append(dst)

    def sync(directory):
        if failure == "asset-sync" and len(asset_targets) == 2:
            raise OSError("synthetic asset sync failure")
        if failure == "note-sync" and (out / "Recipe 1.md").exists():
            raise OSError("synthetic note sync failure")

    monkeypatch.setattr(os, "link", publish)
    monkeypatch.setattr("kptncook.exporter_utils.fsync_directory", sync)

    def parent_sync(directory):
        if failure == "asset-parent-sync" and (out / "Recipe 0.md").exists():
            raise OSError("synthetic asset parent sync failure")
        sync(directory)

    monkeypatch.setattr("kptncook.markdown_exporter.fsync_directory", parent_sync)
    with pytest.raises(UserFacingError) as caught:
        exporter.export(recipes)
    error = caught.value
    assert error.completed_paths == (out / "Recipe 0.md",)
    assert error.completed_assets == (asset_targets[0],)
    assert error.failed_recipe_id == "synthetic-1"
    assert error.remaining_count == 1
    assert (out / "Recipe 0.md").exists()
    assert asset_targets[0].read_bytes() == b"synthetic"
    if failure == "asset-sync":
        assert error.uncertain_paths == (asset_targets[1],)
        assert error.retained_assets == ()
    elif failure == "note-sync":
        assert error.uncertain_paths == (out / "Recipe 1.md",)
        assert error.retained_assets == (asset_targets[1],)
    else:
        assert error.uncertain_paths == ()
        assert error.retained_assets == ()
        assert list((out / "assets").iterdir()) == [asset_targets[0]]
    assert not (out / "Recipe 2.md").exists()
    assert not list(out.glob(".kptncook-*"))
