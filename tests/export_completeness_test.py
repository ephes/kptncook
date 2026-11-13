"""Incomplete timers and long localized titles retain complete recipe text."""

import copy
import gzip
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from kptncook.exporter_utils import move_to_free_target
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.mealie import kptncook_to_mealie
from kptncook.models import Recipe
from kptncook.paprika import GeneratedData, PaprikaExporter
from kptncook.tandoor import TandoorExporter


@pytest.mark.parametrize("kind", ["markdown", "tandoor", "paprika", "mealie"])
@pytest.mark.parametrize("timers", [None, [], [{"min_or_exact": 3}], [{}, {"max": 9}]])
def test_missing_timers_preserve_positions_and_punctuation(
    minimal, monkeypatch, kind, timers
):
    data = copy.deepcopy(minimal)
    text = "Erst <timer>, dann <timer>! Zuletzt <timer>."
    data["steps"][0].update(title={"de": text}, timers=timers)
    recipe = Recipe.model_validate(data)
    before = recipe.model_dump()
    monkeypatch.setattr(
        PaprikaExporter,
        "get_generated_data",
        lambda *a, **kw: GeneratedData(None, None, "synthetic", "synthetic"),
    )
    # Rendered targets get a code span so the marker is not swallowed as HTML.
    marker = "<timer>" if kind == "paprika" else "`<timer>`"
    expected = text.replace("<timer>", marker)
    if timers == [{"min_or_exact": 3}]:
        expected = expected.replace(marker, "3m" if kind == "markdown" else "3 Min.", 1)
    elif timers == [{}, {"max": 9}]:
        expected = (
            f"Erst {marker}, dann "
            + ("≤9m" if kind == "markdown" else "bis zu 9 Min.")
            + f"! Zuletzt {marker}."
        )
    if kind == "markdown":
        assert f"- {expected}\n" in MarkdownExporter().render_recipe(recipe)
    elif kind == "tandoor":
        assert (
            TandoorExporter().get_recipe_payload(recipe)["steps"][0]["instruction"]
            == expected
        )
    elif kind == "paprika":
        assert (
            json.loads(PaprikaExporter().get_recipe_as_json_string(recipe))[
                "directions"
            ]
            == expected + "\n"
        )
    else:
        assert kptncook_to_mealie(recipe).recipe_instructions[0].text == expected
    assert recipe.model_dump() == before


@pytest.mark.parametrize("kind", ["tandoor", "paprika", "markdown"])
@pytest.mark.parametrize(
    "title",
    ["Recipe" * 150, "料理" * 180, "e\u0301" * 350, "Щ" * 350],
    ids=["ascii", "cjk", "combining", "transliterated"],
)
def test_long_title_exports_remain_complete_and_do_not_clobber(
    minimal, tmp_path, monkeypatch, kind, title
):
    monkeypatch.chdir(tmp_path)
    data = copy.deepcopy(minimal)
    data.update(localizedTitle={"de": title}, steps=[], imageList=[])
    recipe = Recipe.model_validate(data)
    exporter = {
        "tandoor": TandoorExporter,
        "paprika": PaprikaExporter,
        "markdown": MarkdownExporter,
    }[kind]()
    previous = {}
    for _ in range(3):
        result = exporter.export([recipe])
        path = Path(result if kind == "paprika" else result[0])
        assert len(path.name.encode("utf-8")) <= 240
        assert path not in previous
        for old_path, old_bytes in previous.items():
            assert old_path.read_bytes() == old_bytes
        if kind == "markdown":
            frontmatter = path.read_text(encoding="utf-8").split("---", 2)[1]
            assert yaml.safe_load(frontmatter)["title"] == title
        else:
            with ZipFile(path) as archive:
                assert archive.testzip() is None
                if kind == "tandoor":
                    payload = json.loads(archive.read("recipe.json"))
                else:
                    [entry] = archive.namelist()
                    payload = json.loads(gzip.decompress(archive.read(entry)))
                assert payload["name"] == title
        previous[path] = path.read_bytes()
    assert any(f"-{recipe.id.oid}-2" in path.name for path in previous)


def test_filename_budget_reserves_unicode_id_and_counter(tmp_path):
    stem = "料理" * 40
    identifier = "é" * 60
    previous = {}
    for index in range(4):
        source = tmp_path / "source"
        source.write_bytes(bytes([index]))
        target = move_to_free_target(source, tmp_path, stem, ".zip", identifier)
        assert len(target.name.encode("utf-8")) <= 240
        for old, content in previous.items():
            assert old.read_bytes() == content
        previous[target] = target.read_bytes()
    assert any(f"-{identifier}-3.zip" in path.name for path in previous)
