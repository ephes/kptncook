"""Rendered step text and asset-before-note crash durability boundaries."""

import errno
import html
import json
import os
import stat
from html.parser import HTMLParser
from pathlib import Path

import markdown
import pytest

from kptncook.config import get_settings
from kptncook.http_errors import UserFacingError
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.mealie import kptncook_to_mealie
from kptncook.models import Recipe
from kptncook.paprika import GeneratedData, PaprikaExporter
from kptncook.tandoor import TandoorExporter


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_data(self, data):
        self.text.append(data)


@pytest.mark.parametrize("kind", ["markdown", "mealie", "tandoor", "paprika"])
def test_source_tags_remain_literal_at_payload_boundary(minimal, monkeypatch, kind):
    source = 'Mix <b>carefully</b> & keep < 5. <img src=x onerror="invented()"> <timer> then <timer>.'
    minimal["steps"][0].update(title={"de": source}, timers=[{"min_or_exact": 3}])
    recipe = Recipe.model_validate(minimal)
    before = recipe.model_dump()
    if kind == "markdown":
        payload = (
            MarkdownExporter().render_recipe(recipe).split("### Zubereitung", 1)[-1]
        )
        duration = "3m"
    elif kind == "mealie":
        payload = (
            kptncook_to_mealie(recipe, api_key="synthetic").recipe_instructions[0].text
        )
        duration = "3 Min."
    elif kind == "tandoor":
        payload = TandoorExporter().get_recipe_payload(recipe)["steps"][0][
            "instruction"
        ]
        duration = "3 Min."
    else:
        monkeypatch.setattr(
            PaprikaExporter,
            "get_generated_data",
            lambda *a, **kw: GeneratedData(None, None, "synthetic", "synthetic"),
        )
        payload = json.loads(PaprikaExporter().get_recipe_as_json_string(recipe))[
            "directions"
        ]
        assert payload == source.replace("<timer>", "3 Min.", 1) + "\n"
        return
    assert html.escape('<img src=x onerror="invented()">', quote=False) in payload
    assert "`<timer>`" in payload
    parser = Elements()
    parser.feed(markdown.markdown(payload))
    assert "img" not in parser.tags
    assert "b" not in parser.tags
    assert "timer" not in parser.tags
    visible = "".join(parser.text)
    assert '<img src=x onerror="invented()">' in visible
    assert "<b>carefully</b> & keep < 5" in visible
    assert duration in visible
    assert "<timer>" in visible
    assert recipe.model_dump() == before


@pytest.mark.parametrize("failure", [None, "asset", "note", "interrupt"])
def test_export_directory_sync_orders_assets_before_note(minimal, monkeypatch, failure):
    out = get_settings().root / "export_md"
    assets = out / "assets"
    assets.mkdir(parents=True)
    old_asset, old_note = assets / "old.jpg", out / "old.md"
    old_asset.write_bytes(b"old image")
    old_note.write_bytes(b"old note")
    monkeypatch.setattr(
        "kptncook.markdown_exporter.download_image", lambda url: b"new image"
    )
    sync, link = os.fsync, os.link
    events = []

    def record_link(src, dst):
        kind = "note" if Path(dst).suffix == ".md" else "asset"
        if kind == "note":
            assert "asset-sync" in events, "Referring note linked before durable asset"
        result = link(src, dst)
        events.append(kind + "-link")
        return result

    def record_sync(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            kind = "asset" if info.st_ino == assets.stat().st_ino else "note"
            if kind + "-link" in events:
                events.append(kind + "-sync")
                if failure == "interrupt" and kind == "note":
                    raise KeyboardInterrupt
                if failure == kind:
                    raise OSError(errno.EIO, "synthetic disk fault")
        return sync(fd)

    monkeypatch.setattr(os, "link", record_link)
    monkeypatch.setattr(os, "fsync", record_sync)
    if failure:
        with pytest.raises(UserFacingError, match="published.*durability.*uncertain"):
            MarkdownExporter().export([Recipe.model_validate(minimal)])
    else:
        [note] = MarkdownExporter().export([Recipe.model_validate(minimal)])
        assert (
            events.index("asset-link")
            < events.index("asset-sync")
            < events.index("note-link")
            < events.index("note-sync")
        )
        assert note.read_text(encoding="utf-8").count("assets/") == 2
    assert old_asset.read_bytes() == b"old image"
    assert old_note.read_bytes() == b"old note"
    new_assets = [p for p in assets.iterdir() if p != old_asset]
    assert len(new_assets) == 1
    assert new_assets[0].read_bytes() == b"new image"
    new_notes = [p for p in out.glob("*.md") if p != old_note]
    assert len(new_notes) == (0 if failure == "asset" else 1)
    if new_notes:
        assert f"assets/{new_assets[0].name}" in new_notes[0].read_text(
            encoding="utf-8"
        )
    assert not list(out.glob(".kptncook-*"))
