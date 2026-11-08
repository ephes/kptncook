"""Synthetic identities cannot become export paths; optional covers stay optional."""

import copy
import gzip
import json
from zipfile import ZipFile

import httpx
import pytest

from kptncook.config import get_settings
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.http_errors import UserFacingError
from kptncook.models import Recipe
from kptncook.paprika import GeneratedData, PaprikaExporter
from kptncook.tandoor import TandoorExporter


@pytest.mark.parametrize(
    "oid", ["../escape", "a/b", "a\\b", "nul\0id", ".", "..", "a" * 256]
)
@pytest.mark.parametrize("kind", ["paprika", "tandoor", "markdown"])
def test_invalid_stored_identity_refused_before_any_export_write(
    minimal, tmp_path, monkeypatch, oid, kind
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        PaprikaExporter,
        "get_generated_data",
        lambda *a, **kw: GeneratedData(None, None, "synthetic", "synthetic"),
    )
    first = copy.deepcopy(minimal)
    first.update(_id={"$oid": "valid-opaque"}, steps=[], imageList=[])
    last = copy.deepcopy(first)
    last["_id"]["$oid"] = oid
    recipes = [Recipe.model_validate(first), Recipe.model_validate(last)]
    exporter = {
        "paprika": PaprikaExporter,
        "tandoor": TandoorExporter,
        "markdown": MarkdownExporter,
    }[kind]()
    with pytest.raises(UserFacingError, match="identifier"):
        exporter.export(recipes)
    assert list(tmp_path.iterdir()) == []
    assert not (get_settings().root / "export_md").exists()


@pytest.mark.parametrize("mode", ["absent", "ambiguous", "404", "valid"])
def test_paprika_optional_cover_keeps_every_text_recipe(
    minimal, tmp_path, monkeypatch, mode
):
    monkeypatch.chdir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(scratch))
    first = copy.deepcopy(minimal)
    first.update(
        _id={"$oid": "opaque-first"},
        localizedTitle={"de": "Synthetic first"},
        steps=[],
        imageList=[],
    )
    second = copy.deepcopy(first)
    second.update(
        _id={"$oid": "opaque-second"}, localizedTitle={"de": "Synthetic second"}
    )
    cover = {"name": "cover.jpg", "type": "cover", "url": "https://example.test/cover"}
    if mode != "absent":
        first["imageList"] = [cover] * (2 if mode == "ambiguous" else 1)
    requests = []

    def download(url):
        requests.append(url)
        if mode == "404":
            response = httpx.Response(404, request=httpx.Request("GET", url))
            response.raise_for_status()
        return b"synthetic-image"

    monkeypatch.setattr("kptncook.paprika.download_image", download)
    filename = PaprikaExporter().export(
        [Recipe.model_validate(first), Recipe.model_validate(second)]
    )
    with ZipFile(filename) as archive:
        assert len(archive.namelist()) == 2
        rows = [
            json.loads(gzip.decompress(archive.read(name)))
            for name in archive.namelist()
        ]
    by_id = {row["uid"]: row for row in rows}
    assert by_id["opaque-first"]["name"] == "Synthetic first"
    assert by_id["opaque-second"]["name"] == "Synthetic second"
    assert bool(by_id["opaque-first"]["photo_data"]) == (mode == "valid")
    assert by_id["opaque-first"]["photo"] == ("cover.jpg" if mode == "valid" else "")
    assert by_id["opaque-second"]["photo_data"] == ""
    assert by_id["opaque-second"]["photo"] == ""
    assert len(requests) == (1 if mode in {"404", "valid"} else 0)
    assert list(scratch.iterdir()) == []


def test_direct_paprika_writer_preflights_all_ids(tmp_path):
    with pytest.raises(UserFacingError, match="identifier"):
        PaprikaExporter().save_recipes(
            {"good": "{}", "a/b": "{}"}, "all.paprikarecipes", str(tmp_path)
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["paprika", "tandoor", "markdown"])
def test_unsafe_id_is_a_controlled_cli_error(minimal, tmp_path, monkeypatch, kind):
    from datetime import date
    from typer.testing import CliRunner
    from kptncook import cli
    from kptncook.repositories import RecipeInDb, RecipeRepository
    from kptncook.services import repository

    repo = RecipeRepository(tmp_path)
    minimal.update(_id={"$oid": "../synthetic-private-id"}, steps=[], imageList=[])
    repo.add(RecipeInDb(date=date(2026, 1, 1), data=minimal))
    monkeypatch.setattr(repository, "get_repository", lambda: repo)
    result = CliRunner().invoke(cli, [f"export-recipes-to-{kind}"])
    assert result.exit_code == 1
    assert "identifier" in result.output
    assert "synthetic-private-id" not in result.output
