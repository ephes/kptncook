"""Offline failures retain exact receipts for already published recipes."""

import errno
import importlib
import logging
import os
import stat
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kptncook import durability, exporter_utils, markdown_exporter
from kptncook.config import get_settings
from kptncook.exporter_utils import (
    BatchExportError,
    ExportPublicationUncertain,
    move_to_free_target,
)
from kptncook.http_errors import UserFacingError
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.models import Recipe
from kptncook.tandoor import TandoorExporter


@pytest.mark.parametrize("sync_errno", [None, errno.ENOTSUP, errno.EIO])
def test_directory_close_failure_preserves_export_outcome(
    tmp_path, monkeypatch, mocker, sync_errno
):
    source = tmp_path / "source"
    source.write_bytes(b"synthetic export")
    target = tmp_path / "published.zip"
    fsync = os.fsync
    close = os.close
    sync_error = OSError(sync_errno, "synthetic sync failure")

    def sync(fd):
        if sync_errno is not None and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise sync_error
        fsync(fd)

    def close_with_error(fd):
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        close(fd)
        if is_directory:
            raise OSError(errno.EIO, "synthetic close failure")

    warning = mocker.patch.object(
        logging.getLogger(durability.__name__), "warning", side_effect=RuntimeError
    )
    monkeypatch.setattr(durability.os, "fsync", sync)
    monkeypatch.setattr(durability.os, "close", close_with_error)

    if sync_errno == errno.EIO:
        with pytest.raises(ExportPublicationUncertain) as caught:
            move_to_free_target(source, tmp_path, "published", ".zip")
        assert caught.value.target == target
        assert caught.value.__cause__ is sync_error
    else:
        assert move_to_free_target(source, tmp_path, "published", ".zip") == target

    warning.assert_called_once_with("Could not close directory sync descriptor.")
    assert target.read_bytes() == b"synthetic export"


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
        exporter, "_download_recipe_image", lambda recipe, **kw: (b"synthetic", "jpg")
    )
    out = get_settings().root / "export_md"
    render = exporter.render_recipe
    current = []

    def rendering(recipe, image_path=None, *, export_date=None):
        current.append(recipe.id.oid)
        if recipe is recipes[1] and failure == "render":
            raise ValueError("synthetic render failure")
        return render(recipe, image_path, export_date=export_date)

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


@pytest.mark.parametrize("later_failure", [False, True])
def test_markdown_counts_published_pair_when_stage_cleanup_fails(
    recipes, monkeypatch, caplog, later_failure
):
    exporter = MarkdownExporter()
    out = get_settings().root / "export_md"
    first_note = out / "Recipe 0.md"
    second_note = out / "Recipe 1.md"
    cleanup = tempfile.TemporaryDirectory.cleanup
    cleaned = []
    first_assets = []

    def download(recipe, **kwargs):
        if later_failure and recipe is recipes[1]:
            raise UserFacingError("Synthetic second recipe failure.")
        return b"synthetic", "jpg"

    def fail_first_cleanup(directory):
        cleanup(directory)
        cleaned.append(directory.name)
        if len(cleaned) == 1:
            assert first_note.is_file()
            [asset] = (out / "assets").iterdir()
            assert asset.read_bytes() == b"synthetic"
            first_assets.append(asset)
            raise PermissionError("private synthetic cleanup detail")

    monkeypatch.setattr(exporter, "_download_recipe_image", download)
    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", fail_first_cleanup)

    if later_failure:
        with pytest.raises(BatchExportError) as caught:
            exporter.export(recipes)
        error = caught.value
        assert error.completed_paths == (first_note,)
        assert error.completed_assets == tuple(first_assets)
        assert error.failed_recipe_id == recipes[1].id.oid
        assert error.remaining_count == 1
        assert list(out.glob("*.md")) == [first_note]
    else:
        assert exporter.export(recipes[:2]) == [first_note, second_note]
        assert set(out.glob("*.md")) == {first_note, second_note}

    [first_asset] = first_assets
    assert first_asset.relative_to(out).as_posix() in first_note.read_text("utf-8")
    assert caplog.text.count("Could not clean up Markdown staging directory.") == 1
    assert "private synthetic cleanup detail" not in caplog.text


@pytest.mark.parametrize(
    ("staging", "publication"),
    [
        ("source", "success"),
        ("sibling", "success"),
        ("sibling", "failed"),
        ("sibling", "uncertain"),
    ],
)
def test_markdown_cleanup_logging_preserves_publication(
    recipes, monkeypatch, mocker, staging, publication
):
    exporter = MarkdownExporter()
    monkeypatch.setattr(
        exporter, "_download_recipe_image", lambda recipe, **kw: (b"synthetic", "jpg")
    )
    out = get_settings().root / "export_md"
    note = out / "Recipe 0.md"
    unlink = Path.unlink
    link = os.link
    sync = exporter_utils.fsync_directory

    def fail_staging_unlink(path, *args, **kwargs):
        if (staging == "source" and path.name == "note.md") or (
            staging == "sibling"
            and path.parent == out
            and path.name.startswith(".kptncook-")
            and path.suffix == ".tmp"
        ):
            raise OSError("synthetic staging unlink failure")
        return unlink(path, *args, **kwargs)

    def publish(src, dst):
        if publication == "failed" and dst == note:
            raise OSError("synthetic note publication failure")
        link(src, dst)

    def sync_publication(directory):
        if publication == "uncertain" and directory == out:
            raise OSError("synthetic directory sync failure")
        sync(directory)

    monkeypatch.setattr(Path, "unlink", fail_staging_unlink)
    monkeypatch.setattr(os, "link", publish)
    monkeypatch.setattr(exporter_utils, "fsync_directory", sync_publication)
    warning = mocker.patch.object(
        exporter_utils.logger, "warning", side_effect=RuntimeError("logging failed")
    )

    if publication == "success":
        assert exporter.export(recipes[:1]) == [note]
    else:
        with pytest.raises(BatchExportError) as caught:
            exporter.export(recipes[:1])
        if publication == "failed":
            assert "synthetic note publication failure" in str(caught.value.__cause__)
            assert caught.value.uncertain_paths == ()
        else:
            assert caught.value.uncertain_paths == (note,)

    warning.assert_called_once()
    if publication == "failed":
        assert not note.exists()
        assert list((out / "assets").iterdir()) == []
    else:
        [asset] = (out / "assets").iterdir()
        assert asset.relative_to(out).as_posix() in note.read_text(encoding="utf-8")
        assert asset.read_bytes() == b"synthetic"
        if publication == "uncertain":
            assert caught.value.retained_assets == (asset,)


@pytest.mark.parametrize("rollback", ["ok", "sync-fails", "logging-fails"])
def test_markdown_syncs_asset_removal_after_note_failure(
    recipes, monkeypatch, caplog, mocker, rollback
):
    fail_rollback_sync = rollback != "ok"
    exporter = MarkdownExporter()
    monkeypatch.setattr(
        exporter, "_download_recipe_image", lambda recipe, **kw: (b"synthetic", "jpg")
    )
    out = get_settings().root / "export_md"
    assets = out / "assets"
    synced = []
    note_error = OSError("synthetic note staging failure")

    def sync(directory):
        if directory == assets:
            synced.append(list(assets.glob("*.jpg")))
            if fail_rollback_sync and len(synced) == 2:
                raise OSError("synthetic asset rollback sync failure")

    write_bytes = Path.write_bytes

    def fail_note(path, *args, **kwargs):
        if path.name != "note.md":
            return write_bytes(path, *args, **kwargs)
        assert len(synced) == 1
        assert len(synced[0]) == 1
        assert synced[0][0].is_file()
        raise note_error

    monkeypatch.setattr("kptncook.exporter_utils.fsync_directory", sync)
    monkeypatch.setattr("kptncook.markdown_exporter.fsync_directory", sync)
    monkeypatch.setattr(Path, "write_bytes", fail_note)
    if rollback == "logging-fails":
        warning = mocker.patch.object(
            markdown_exporter.logger,
            "warning",
            side_effect=RuntimeError("logging failed"),
        )
    with pytest.raises(BatchExportError) as caught:
        exporter.export(recipes[:1])
    assert caught.value.__cause__ is note_error
    assert len(synced) == 2
    assert synced[1] == []
    assert list(out.iterdir()) == [assets]
    assert list(assets.iterdir()) == []
    assert ("Markdown asset cleanup is uncertain" in caplog.text) == (
        rollback == "sync-fails"
    )
    if rollback == "sync-fails":
        assert str(synced[0][0]) in caplog.text
    if rollback == "logging-fails":
        warning.assert_called_once()
