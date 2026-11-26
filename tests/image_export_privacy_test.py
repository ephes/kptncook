"""Authenticated requests stay out of portable Markdown artifacts."""

from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
import yaml

from kptncook.config import get_settings
from kptncook.exporter_utils import BatchExportError
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.models import Image, Recipe


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/image",
        "https://example.test/image?sig=signed&x=1&x=2&empty=#fragment",
        "https://example.test/image?kptnkey=old&x=1&kptnkey=again#fragment",
    ],
)
def test_image_auth_query_composition(minimal, url):
    key = "synthetic key&?=#/"
    image = Image(name="synthetic.jpg", type="cover", url=url)
    minimal["imageList"] = [image.model_dump()]
    recipe = Recipe.model_validate(minimal)
    for authenticated in (
        image.get_image_with_api_key_url(key).url,
        recipe.get_image_url(key),
    ):
        parsed = urlsplit(authenticated)
        assert parsed.fragment == urlsplit(url).fragment
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        assert [(k, v) for k, v in pairs if k == "kptnkey"] == [("kptnkey", key)]
        assert [(k, v) for k, v in pairs if k != "kptnkey"] == [
            (k, v)
            for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True)
            if k != "kptnkey"
        ]
    assert image.url == url


@pytest.mark.parametrize("failure", [None, "404", "timeout"])
@pytest.mark.parametrize("steps", [False, True])
def test_markdown_images_are_local_private_and_optional(
    minimal, monkeypatch, caplog, failure, steps
):
    key = "synthetic-secret-77"
    monkeypatch.setenv("KPTNCOOK_API_KEY", key)
    image = {
        "name": f"{key}.jpg",
        "type": "cover",
        "url": "https://example.test/image?signature=private&x=1#part",
    }
    minimal["imageList"] = [image]
    if steps:
        minimal["steps"][0]["image"] = image
    else:
        minimal["steps"] = []
    recipe = Recipe.model_validate(minimal)
    requests = []

    def download(url, **kwargs):
        requests.append(url)
        assert dict(parse_qsl(urlsplit(url).query))["kptnkey"] == key
        if failure == "404":
            response = httpx.Response(404, request=httpx.Request("GET", url))
            raise httpx.HTTPStatusError(
                f"private {key} {url}", request=response.request, response=response
            )
        if failure == "timeout":
            raise httpx.ReadTimeout(f"private {key} {url}")
        return b"\xff\xd8\xffsynthetic-image-bytes"

    monkeypatch.setattr(
        "kptncook.markdown_exporter.download_image", download, raising=False
    )
    [path] = MarkdownExporter().export([recipe])
    text = path.read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---", 2)[1])
    assert requests
    assert "signature" not in text
    assert key not in text + caplog.text
    assert "kptnkey" not in text
    for item in path.parent.rglob("*"):
        assert key not in item.name
    if failure:
        assert "image" not in front
        assert "![" not in text
        assert not list(path.parent.rglob("*.jpg"))
    else:
        relative = Path(front["image"])
        assert not relative.is_absolute()
        assert (
            path.parent / relative
        ).read_bytes() == b"\xff\xd8\xffsynthetic-image-bytes"
        assert f"]({relative.as_posix()})" in text
        assert relative.parts[0] == "assets"
    assert "###" in text


@pytest.mark.parametrize(
    ("step_url", "chosen_path"),
    [
        ("", "/cover"),
        ("https://example.test/step", "/step"),
        ("https://off-list.invalid/step", None),
    ],
)
def test_markdown_selects_cover_only_when_final_step_image_is_missing(
    minimal, monkeypatch, step_url, chosen_path
):
    minimal["steps"].append(
        {
            "title": {"de": "Serve"},
            "image": {"name": "step.jpg", "url": step_url, "type": "step"},
        }
    )
    minimal["imageList"] = [
        {"name": "cover.jpg", "url": "https://example.test/cover", "type": "cover"}
    ]
    recipe = Recipe.model_validate(minimal)
    downloaded = []

    def download(url, **kwargs):
        downloaded.append(urlsplit(url).path)
        return b"\xff\xd8\xffsynthetic image"

    monkeypatch.setattr("kptncook.markdown_exporter.download_image", download)
    [note] = MarkdownExporter().export([recipe])
    text = note.read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---", 2)[1])
    if chosen_path is None:
        assert downloaded == []
        assert "image" not in front
        assert list(note.parent.rglob("*.jpg")) == []
    else:
        assert downloaded == [chosen_path]
        asset = note.parent / front["image"]
        assert list(note.parent.rglob("*.jpg")) == [asset]
        assert asset.read_bytes() == b"\xff\xd8\xffsynthetic image"
        assert f"]({front['image']})" in text


def test_render_without_assets_has_no_remote_image(minimal):
    text = MarkdownExporter().render_recipe(Recipe.model_validate(minimal))
    assert "kptnkey" not in text
    assert "image:" not in text


def test_failed_asset_write_cleans_only_owned_output(minimal, monkeypatch):
    monkeypatch.setattr(
        "kptncook.markdown_exporter.download_image",
        lambda url, **kw: b"\xff\xd8\xffimage",
    )
    output = get_settings().root / "export_md"
    assets = output / "assets"
    assets.mkdir(parents=True)
    sentinel = assets / "existing.jpg"
    sentinel.write_bytes(b"keep")
    original_open = Path.open
    attempted = []

    class FailingAsset:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def write(self, data):
            self.handle.write(b"partial")
            raise OSError("Synthetic write failure")

    def open_asset(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if mode == "xb" and path.suffix == ".jpg":
            attempted.append(path)
            return FailingAsset(handle)
        return handle

    monkeypatch.setattr(Path, "open", open_asset)
    with pytest.raises(BatchExportError, match="OSError"):
        MarkdownExporter().export([Recipe.model_validate(minimal)])
    assert attempted
    assert not list(output.glob("*.md"))
    assert list(assets.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"keep"


def test_auth_preserves_signed_query_bytes():
    query = "sig=a%20b/c:,&flag&x=%2f&kptn%6bey=old&x=two+words"
    image = Image(name="synthetic", url=f"https://example.test/image?{query}#fragment")
    result = image.get_image_with_api_key_url("new key")
    assert (
        urlsplit(result.url).query
        == "sig=a%20b/c:,&flag&x=%2f&x=two+words&kptnkey=new+key"
    )
