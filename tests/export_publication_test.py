"""Synthetic publication boundaries never expose incomplete final artifacts."""

import gc
import gzip
import json
import shutil
import weakref
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from kptncook.config import get_settings
from kptncook.exporter_utils import move_to_free_target
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
            self.handle.write(data[:2])
            self.handle.flush()
            observed.append(True)
            assert not list(out.glob("*.md"))
            self.handle.write(data[2:])

    def observe(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return (
            ObservedAsset(handle) if mode == "xb" and path.suffix == ".jpg" else handle
        )

    monkeypatch.setattr(Path, "open", observe)
    monkeypatch.setattr(
        "kptncook.markdown_exporter.download_image", lambda url: b"synthetic image"
    )
    [note] = MarkdownExporter().export([Recipe.model_validate(minimal)])
    assert observed
    [asset] = (out / "assets").iterdir()
    assert asset.read_bytes() == b"synthetic image"
    assert f"assets/{asset.name}" in note.read_text(encoding="utf-8")
    assert sorted(p.name for p in out.iterdir()) == sorted(["assets", note.name])


def test_paprika_releases_each_serialized_recipe(minimal, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    exporter = PaprikaExporter()
    live = []
    original_serialize = exporter.get_recipe_as_json_string
    photo = "c3ludGhldGlj" * 1024
    monkeypatch.setattr(
        exporter,
        "get_generated_data",
        lambda recipe: GeneratedData("cover.jpg", photo, "2026-01-01", "synthetic"),
    )

    class TrackedJSON(str):
        pass

    def serialize(recipe):
        gc.collect()
        assert not any(ref() is not None for ref in live)
        value = TrackedJSON(original_serialize(recipe))
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

    def serialize(recipe):
        calls.append(recipe.id.oid)
        if len(calls) == 2:
            raise OSError("synthetic failure")
        return "{}"

    monkeypatch.setattr(
        PaprikaExporter,
        "get_recipe_as_json_string",
        lambda self, recipe: serialize(recipe),
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
        "kptncook.markdown_exporter.download_image", lambda url: b"synthetic"
    )
    with pytest.raises(OSError, match="synthetic publication failure"):
        MarkdownExporter().export([Recipe.model_validate(minimal)])
    assert attempted
    assert list(assets.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"keep"
    assert list(out.iterdir()) == [assets]
