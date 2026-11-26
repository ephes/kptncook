"""Synthetic publication boundaries never expose incomplete final artifacts."""

import errno
import gc
import gzip
import json
import os
import shutil
import weakref
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from kptncook import exporter_utils
from kptncook.config import get_settings
from kptncook.exporter_utils import BatchExportError, move_to_free_target
from kptncook.http_errors import UserFacingError
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.models import Recipe
from kptncook.paprika import GeneratedData, PaprikaExporter


def test_archive_name_absent_during_copy(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.write_bytes(b"complete synthetic archive")
    out = tmp_path / "out"
    out.mkdir()
    copy = shutil.copyfileobj
    observed = []

    def paused_copy(src, dst):
        dst.write(src.read(3))
        dst.flush()
        observed.append(True)
        assert not list(out.glob("*.zip"))
        copy(src, dst)

    monkeypatch.setattr(shutil, "copyfileobj", paused_copy)
    target = move_to_free_target(source, out, "name", ".zip")
    assert observed
    assert target.read_bytes() == b"complete synthetic archive"
    assert list(out.iterdir()) == [target]


def test_concurrent_archive_publishers_preserve_both(tmp_path):
    sources = [tmp_path / f"source-{i}" for i in range(2)]
    for i, source in enumerate(sources):
        source.write_bytes(bytes([i]) * 1024)
    with ThreadPoolExecutor(max_workers=2) as pool:
        targets = list(
            pool.map(
                lambda src: move_to_free_target(src, tmp_path, "name", ".zip"), sources
            )
        )
    assert len(set(targets)) == 2
    assert {target.read_bytes() for target in targets} == {
        bytes([i]) * 1024 for i in range(2)
    }


def test_markdown_note_absent_until_asset_complete(minimal, monkeypatch):
    out = get_settings().root / "export_md"
    original_open = Path.open
    observed = []

    class ObservedAsset:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def write(self, data):
            first_count = self.handle.write(data[:2])
            self.handle.flush()
            observed.append(True)
            assert not list(out.glob("*.md"))
            return first_count + self.handle.write(data[2:])

    def observe(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return (
            ObservedAsset(handle) if mode == "xb" and path.suffix == ".jpg" else handle
        )

    monkeypatch.setattr(Path, "open", observe)
    monkeypatch.setattr(
        "kptncook.markdown_exporter.download_image",
        lambda url, **kw: b"\xff\xd8\xffsynthetic image",
    )
    [note] = MarkdownExporter().export([Recipe.model_validate(minimal)])
    assert observed
    [asset] = (out / "assets").iterdir()
    assert asset.read_bytes() == b"\xff\xd8\xffsynthetic image"
    assert f"assets/{asset.name}" in note.read_text(encoding="utf-8")
    assert sorted(p.name for p in out.iterdir()) == sorted(["assets", note.name])


@pytest.mark.parametrize(
    ("short_stage", "kind"), [("image.jpg", "image"), ("note.md", "note")]
)
def test_markdown_short_stage_preserves_completed_exports(
    minimal, monkeypatch, short_stage, kind
):
    out = get_settings().root / "export_md"
    assets = out / "assets"
    assets.mkdir(parents=True)
    existing_note = out / "Minimal Recipe.md"
    existing_asset = assets / "existing.jpg"
    existing_note.write_bytes(b"existing note")
    existing_asset.write_bytes(b"existing image")
    image = b"\xff\xd8\xffsynthetic image"
    monkeypatch.setattr(
        "kptncook.markdown_exporter.download_image", lambda *args, **kwargs: image
    )
    original_open = Path.open
    writes = 0

    def open_with_short_write(path, mode="r", *args, **kwargs):
        nonlocal writes
        stream = original_open(path, mode, *args, **kwargs)
        if path.name == short_stage and mode in {"xb", "wb"}:
            writes += 1
            if writes == 2:
                complete_write = stream.write
                stream.write = lambda data: complete_write(data[:-1])
        return stream

    monkeypatch.setattr(Path, "open", open_with_short_write)
    recipes = [
        Recipe.model_validate(dict(minimal, _id={"$oid": f"{i:024x}"}))
        for i in range(1, 4)
    ]

    with pytest.raises(
        BatchExportError, match=f"Incomplete Markdown {kind} staging write"
    ) as caught:
        MarkdownExporter().export(recipes)

    error = caught.value
    assert writes == 2
    assert error.failed_recipe_id == recipes[1].id.oid
    assert error.remaining_count == 1
    assert error.retained_assets == ()
    [note] = error.completed_paths
    [asset] = error.completed_assets
    assert asset.read_bytes() == image
    assert f"assets/{asset.name}" in note.read_text(encoding="utf-8")
    assert set(out.iterdir()) == {assets, note, existing_note}
    assert set(assets.iterdir()) == {asset, existing_asset}
    assert existing_note.read_bytes() == b"existing note"
    assert existing_asset.read_bytes() == b"existing image"


def test_paprika_releases_each_serialized_recipe(minimal, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    exporter = PaprikaExporter()
    live = []
    original_serialize = exporter.get_recipe_as_json_string
    photo = "c3ludGhldGlj" * 1024
    monkeypatch.setattr(
        exporter,
        "get_generated_data",
        lambda recipe, **kw: GeneratedData(
            "cover.jpg", photo, "2026-01-01", "synthetic"
        ),
    )

    class TrackedJSON(str):
        pass

    def serialize(recipe, **kwargs):
        gc.collect()
        assert not any(ref() is not None for ref in live)
        value = TrackedJSON(original_serialize(recipe, **kwargs))
        live.append(weakref.ref(value))
        return value

    monkeypatch.setattr(exporter, "get_recipe_as_json_string", serialize)
    recipes = [
        Recipe.model_validate(dict(minimal, _id={"$oid": f"{i:024x}"}))
        for i in range(100)
    ]
    filename = exporter.export(recipes)
    assert len(live) == 100
    with zipfile.ZipFile(filename) as archive:
        assert len(archive.namelist()) == 100
        for recipe in recipes:
            data = json.loads(
                gzip.decompress(archive.read(f"recipe_{recipe.id.oid}.paprikarecipe"))
            )
            assert data["uid"] == recipe.id.oid
            assert data["photo_data"] == photo
            assert data["name"] == "Minimal Recipe"


def test_paprika_mid_batch_failure_publishes_nothing(minimal, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def serialize(recipe, **kwargs):
        calls.append(recipe.id.oid)
        if len(calls) == 2:
            raise OSError("synthetic failure")
        return "{}"

    monkeypatch.setattr(
        PaprikaExporter,
        "get_recipe_as_json_string",
        lambda self, recipe, **kw: serialize(recipe, **kw),
    )
    recipes = [
        Recipe.model_validate(dict(minimal, _id={"$oid": f"{i:024x}"}))
        for i in range(2)
    ]
    with pytest.raises(OSError, match="synthetic failure"):
        PaprikaExporter().export(recipes)
    assert len(calls) == 2
    assert not list(tmp_path.iterdir())


def test_failed_note_publication_removes_only_owned_asset(minimal, monkeypatch):
    import os

    out = get_settings().root / "export_md"
    assets = out / "assets"
    assets.mkdir(parents=True)
    sentinel = assets / "previous.jpg"
    sentinel.write_bytes(b"keep")
    original_link = os.link
    attempted = []

    def fail_note(source, target):
        if Path(target).suffix == ".md":
            attempted.append(True)
            assert Path(source).read_text(encoding="utf-8").endswith("- ")
            assert len(list(assets.iterdir())) == 2
            raise OSError("synthetic publication failure")
        return original_link(source, target)

    monkeypatch.setattr(os, "link", fail_note)
    monkeypatch.setattr(
        "kptncook.markdown_exporter.download_image",
        lambda url, **kw: b"\xff\xd8\xffsynthetic",
    )
    with pytest.raises(UserFacingError, match="synthetic publication failure"):
        MarkdownExporter().export([Recipe.model_validate(minimal)])
    assert attempted
    assert list(assets.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"keep"
    assert list(out.iterdir()) == [assets]


@pytest.mark.parametrize(
    "failure",
    [
        PermissionError(errno.EPERM, "Operation not permitted"),
        OSError(errno.ENOTSUP, "Operation not supported"),
        OSError(errno.EXDEV, "Cross-device link"),
        None,  # platform without os.link
    ],
)
def test_unsupported_hard_links_refuse_without_publishing(
    tmp_path, monkeypatch, failure
):
    source = tmp_path / "source.bin"
    source.write_bytes(b"complete synthetic archive")
    out = tmp_path / "out"
    out.mkdir()
    if failure is None:
        monkeypatch.delattr(os, "link")
    else:

        def refuse_link(src, dst):
            raise failure

        monkeypatch.setattr(os, "link", refuse_link)

    with pytest.raises(UserFacingError, match="must support hard links"):
        move_to_free_target(source, out, "name", ".zip")

    assert list(out.iterdir()) == []  # no final file, no staging leftover
    assert source.exists()  # caller-owned source is not consumed


def test_markdown_refuses_unsupported_hard_links_through_cli(minimal, monkeypatch):
    from importlib import import_module

    from typer.testing import CliRunner

    cli = import_module("kptncook.cli")

    get_settings()
    recipe = Recipe.model_validate(minimal)
    monkeypatch.setattr(
        "kptncook.markdown_exporter.download_image",
        lambda *a, **kw: b"\xff\xd8\xffsynthetic",
    )
    monkeypatch.setattr(
        cli,
        "export_recipes_to_markdown_workflow",
        lambda recipe_id, servings: MarkdownExporter(servings=servings).export(
            [recipe]
        ),
    )

    def refuse_link(src, dst):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse_link)

    result = CliRunner().invoke(cli.app, ["export-recipes-to-markdown"])

    assert result.exit_code == 1
    # Rich wraps CLI errors at the terminal width; compare word by word.
    assert "must support hard links" in " ".join(result.output.split())
    assert not isinstance(result.exception, PermissionError)
    out_dir = get_settings().root / "export_md"
    assert not list(out_dir.glob("*.md"))
    assets = out_dir / "assets"
    assert not assets.exists() or not list(assets.iterdir())
    assert not list(out_dir.glob(".kptncook-*"))


@pytest.mark.parametrize(
    "failure",
    [
        OSError(errno.ENAMETOOLONG, "File name too long"),
        OSError(errno.ENOSPC, "No space left on device"),
    ],
)
def test_other_link_failures_are_not_reported_as_missing_hard_links(
    tmp_path, monkeypatch, failure
):
    source = tmp_path / "source.bin"
    source.write_bytes(b"complete synthetic archive")
    out = tmp_path / "out"
    out.mkdir()

    def fail_link(src, dst):
        raise failure

    monkeypatch.setattr(os, "link", fail_link)

    with pytest.raises(UserFacingError) as excinfo:
        move_to_free_target(source, out, "name", ".zip")

    message = str(excinfo.value)
    assert "hard links" not in message
    assert failure.strerror in message
    assert "name.zip" in message
    assert list(out.iterdir()) == []
    assert source.exists()


@pytest.mark.parametrize(
    "outcome", ["linked", "linked-exists", "competing", "unreadable"]
)
def test_markdown_reconciles_ambiguous_hard_link(minimal, monkeypatch, outcome):
    image_bytes = b"\xff\xd8\xffsynthetic"
    out = get_settings().root / "export_md"
    note = out / "Minimal Recipe.md"
    link, lstat, sync = os.link, Path.lstat, exporter_utils.fsync_directory
    synced = []

    def lose_link_reply(source, target):
        if target != note:
            return link(source, target)
        if outcome == "competing":
            target.write_bytes(b"unrelated note")
        else:
            link(source, target)
        if outcome == "linked-exists":
            raise FileExistsError(errno.EEXIST, "synthetic retried link")
        raise OSError(errno.EIO, "synthetic link error")

    def inspect(path):
        if path == note and outcome == "unreadable":
            raise OSError(errno.EIO, "synthetic stat error")
        return lstat(path)

    def record_sync(directory):
        synced.append(directory)
        sync(directory)

    monkeypatch.setattr(os, "link", lose_link_reply)
    monkeypatch.setattr(exporter_utils, "fsync_directory", record_sync)
    monkeypatch.setattr(
        "kptncook.markdown_exporter.download_image",
        lambda *a, **kw: image_bytes,
    )
    exporter = MarkdownExporter()
    recipe = Recipe.model_validate(minimal)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", inspect)
        if outcome in {"competing", "unreadable"}:
            with pytest.raises(BatchExportError) as caught:
                exporter.export([recipe])
        else:
            assert exporter.export([recipe]) == [note]
            assert synced == [out / "assets", out]

    assets = list((out / "assets").iterdir())
    assert list(out.glob("*.md")) == [note]
    assert not list(out.glob(".kptncook-*"))
    if outcome == "competing":
        assert note.read_bytes() == b"unrelated note"
        assert caught.value.uncertain_paths == ()
        assert not assets
    else:
        [asset] = assets
        assert asset.read_bytes() == image_bytes
        assert f"assets/{asset.name}" in note.read_text(encoding="utf-8")
        if outcome == "unreadable":
            assert caught.value.uncertain_paths == (note,)
            assert caught.value.retained_assets == (asset,)
            assert "Publication could not be confirmed" in str(caught.value)
