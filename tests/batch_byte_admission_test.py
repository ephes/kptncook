"""Real offline download and publication paths share cumulative allowances."""

import httpx
import pytest

from kptncook import exporter_utils, image_download
from kptncook.config import get_settings
from kptncook.http_errors import UserFacingError
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.models import Recipe
from kptncook.paprika import PaprikaExporter
from kptncook.tandoor import TandoorExporter


@pytest.fixture
def batch(minimal):
    return [
        Recipe.model_validate(
            dict(
                minimal,
                _id={"$oid": f"synthetic-{i}"},
                steps=[],
                localizedTitle={"de": f"Recipe {i}"},
                imageList=[
                    {
                        "name": "cover.jpg",
                        "type": "cover",
                        "url": "https://example.test/cover",
                    }
                ],
            )
        )
        for i in range(5)
    ]


@pytest.mark.parametrize("kind", ["paprika", "tandoor", "markdown"])
@pytest.mark.parametrize("mode", ["partial", "exact", "oversized"])
def test_cumulative_assets_stop_with_receipts(batch, tmp_path, monkeypatch, kind, mode):
    monkeypatch.setattr(
        exporter_utils,
        "MAX_BATCH_ASSET_BYTES",
        8 if mode == "exact" else 12,
        raising=False,
    )
    if mode == "oversized":
        monkeypatch.setattr(image_download, "MAX_IMAGE_BYTES", 6)
    out = tmp_path / "published"
    out.mkdir()
    monkeypatch.chdir(out)
    scratch = tmp_path / "temporary"
    scratch.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(scratch))
    sentinel = out / "old.zip"
    sentinel.write_bytes(b"keep")
    streams = []

    class Stream(httpx.AsyncByteStream):
        def __init__(self):
            self.chunks = 0
            self.closed = False

        async def __aiter__(self):
            for _ in range(2):
                self.chunks += 1
                yield b"1234"

        async def aclose(self):
            self.closed = True

    def handle(request):
        stream = Stream()
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        image_download.httpx,
        "AsyncClient",
        lambda **kw: client_class(transport=httpx.MockTransport(handle), **kw),
    )
    exporter = {
        "paprika": PaprikaExporter,
        "tandoor": TandoorExporter,
        "markdown": MarkdownExporter,
    }[kind]()
    with pytest.raises(UserFacingError, match="asset.*allowance") as caught:
        exporter.export(batch)
    error = caught.value
    assert error.failed_recipe_id == "synthetic-1"
    assert error.remaining_count == 3
    assert error.uncertain_paths == ()
    if kind == "paprika":
        assert error.completed_paths == ()
        assert error.unpublished_count == 5
        assert list(out.iterdir()) == [sentinel]
    elif kind == "tandoor":
        assert error.completed_paths == (out / "Recipe_0.zip",)
        assert sorted(out.iterdir()) == sorted([sentinel, *error.completed_paths])
    else:
        md = get_settings().root / "export_md"
        assert error.completed_paths == (md / "Recipe 0.md",)
        assert len(error.completed_assets) == (0 if mode == "oversized" else 1)
        if error.completed_assets:
            assert error.completed_assets[0].read_bytes() == b"12341234"
        assert list(md.glob("*.md")) == list(error.completed_paths)
        assert list((md / "assets").glob("*.jpg")) == list(error.completed_assets)
        assert not list(md.glob(".kptncook-*"))
    assert all(path.exists() for path in error.completed_paths)
    assert sentinel.read_bytes() == b"keep"
    assert len(streams) == (1 if mode == "exact" else 2)
    assert all(stream.closed and stream.chunks == 2 for stream in streams)
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("kind", ["paprika", "tandoor", "markdown"])
def test_output_allowance_refuses_before_partial_final(
    batch, tmp_path, monkeypatch, kind
):
    monkeypatch.setattr(exporter_utils, "MAX_BATCH_OUTPUT_BYTES", 1, raising=False)
    monkeypatch.chdir(tmp_path)
    for recipe in batch:
        recipe.image_list = []
    exporter = {
        "paprika": PaprikaExporter,
        "tandoor": TandoorExporter,
        "markdown": MarkdownExporter,
    }[kind]()
    with pytest.raises(UserFacingError, match="output.*allowance") as caught:
        exporter.export(batch)
    assert caught.value.completed_paths == ()
    assert caught.value.uncertain_paths == ()
    assert not list(tmp_path.glob("*.zip"))
    assert not list(tmp_path.glob("*.paprikarecipes"))
    md = get_settings().root / "export_md"
    assert not list(md.glob("*.md"))
    assert not list(md.glob(".kptncook-*"))


@pytest.mark.parametrize("kind", ["tandoor", "markdown"])
def test_cumulative_output_retains_first_receipt(batch, tmp_path, monkeypatch, kind):
    # Measure the actual writes of one real export, then allow exactly that work
    # for a multi-recipe batch. Compression/header sizes are platform-dependent.
    for recipe in batch:
        recipe.image_list = []
    totals = []
    charge = exporter_utils.ExportBudget.charge_output

    def observe(self, amount):
        charge(self, amount)
        totals.append(self.output_bytes)

    monkeypatch.setattr(exporter_utils.ExportBudget, "charge_output", observe)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    monkeypatch.chdir(baseline)
    get_settings().root = baseline
    cls = TandoorExporter if kind == "tandoor" else MarkdownExporter
    cls().export(batch[:1])
    monkeypatch.setattr(exporter_utils, "MAX_BATCH_OUTPUT_BYTES", totals[-1])
    out = tmp_path / "actual"
    out.mkdir()
    monkeypatch.chdir(out)
    get_settings().root = out
    with pytest.raises(UserFacingError, match="output.*allowance") as caught:
        cls().export(batch)
    error = caught.value
    assert error.failed_recipe_id == "synthetic-1"
    assert error.remaining_count == 3
    assert error.unpublished_count == 4
    assert len(error.completed_paths) == 1
    assert error.completed_paths[0].is_file()
    assert error.uncertain_paths == ()
    assert error.retained_assets == ()
    directory = out if kind == "tandoor" else out / "export_md"
    assert list(directory.iterdir()) == list(error.completed_paths)
