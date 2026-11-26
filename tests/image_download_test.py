"""Synthetic HTTP streams exercise the shared bound through every exporter."""

import asyncio
import gzip
import json
from zipfile import ZipFile

import httpx
import pytest

from kptncook import exporter_utils, image_download
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.mealie import MealieApiClient, RecipeStep, RecipeWithImage
from kptncook.models import Image, Recipe
from kptncook.paprika import PaprikaExporter
from kptncook.tandoor import TandoorExporter


JPEG_CHUNK = b"\xff\xd8\xff1"


class ImageStream(httpx.AsyncByteStream):
    def __init__(self, failure):
        self.failure = failure
        self.closed = False
        self.chunks = 0
        self.cancelled = False

    async def __aiter__(self):
        try:
            for _ in range({"empty": 0, "short": 2}.get(self.failure, 3)):
                self.chunks += 1
                yield JPEG_CHUNK
            if self.failure in {"slow", "stalled"}:
                while True:
                    self.chunks += 1
                    if self.failure == "stalled":
                        await asyncio.Future()
                    else:
                        await asyncio.sleep(0)
                    yield b"x"
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("caller", ["paprika", "tandoor", "mealie", "markdown"])
@pytest.mark.parametrize(
    "failure",
    [
        "oversize",
        "misleading-length",
        "oversize-length",
        "invalid-length",
        "short",
        "empty",
        "exact-length",
        "slow",
        "stalled",
        "ok",
        "status",
        "compressed",
        "partial-status",
        "content-range",
        "created",
    ],
)
def test_bounded_image_preserves_recipe(
    caller, failure, minimal, monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(
        httpx, "get", lambda *a, **kw: pytest.fail("Buffered download bypasses bounds")
    )
    stream = ImageStream(failure)
    declared_length = {
        "misleading-length": "1",
        "oversize-length": "13",
        "invalid-length": "+12",
        "short": "12",
        "exact-length": "012",
    }.get(failure)
    headers = {"Content-Length": declared_length} if declared_length is not None else {}
    if failure == "ok":
        headers["Transfer-Encoding"] = "chunked"
    if failure == "compressed":
        headers["Content-Encoding"] = "gzip"
    if failure == "content-range":
        headers["Content-Range"] = "bytes 0-11/24"
    status = {"status": 503, "partial-status": 206, "created": 201}.get(failure, 200)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, stream=stream)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(image_download.httpx, "AsyncClient", lambda **kw: http)
    has_image = failure in {"ok", "exact-length"}
    timed = failure in {"slow", "stalled"}
    monkeypatch.setattr(
        image_download,
        "MAX_IMAGE_BYTES",
        10**9 if timed else 8 if failure in {"oversize", "misleading-length"} else 12,
    )
    monkeypatch.setattr(
        image_download, "IMAGE_DOWNLOAD_DEADLINE", 0.01 if timed else 60
    )
    workdir = tmp_path / "temporary"
    workdir.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(workdir))
    monkeypatch.chdir(tmp_path)
    recipe = Recipe.model_validate(minimal)
    recipe.id.oid = "a" * 24
    recipe.localized_title.de = "Synthetic stew"
    recipe.image_list = [
        Image(name="cover.jpg", url="https://images.example/cover.jpg", type="cover")
    ]
    recipe.steps[0].title.de = "Mix synthetic ingredients"
    recipe.steps[0].image = Image(
        name="step.jpg", url="https://images.example/step.jpg", type="step"
    )

    if caller == "paprika":
        filename = PaprikaExporter().export([recipe])
        with ZipFile(filename) as archive:
            payload = json.loads(gzip.decompress(archive.read(archive.namelist()[0])))
        assert payload["name"] == "Synthetic stew"
        assert payload["directions"] == "Mix synthetic ingredients\n"
        assert bool(payload["photo_data"]) == has_image
    elif caller == "tandoor":
        filename = TandoorExporter().export_recipe(recipe)
        with ZipFile(filename) as archive:
            payload = json.loads(archive.read("recipe.json"))
            assert ("image.jpg" in archive.namelist()) == has_image
        assert payload["steps"][0]["instruction"] == "Mix synthetic ingredients"
    elif caller == "markdown":
        # Exercise the single transfer with no fallback candidate.
        recipe.image_list = []
        [path] = MarkdownExporter().export([recipe])
        text = path.read_text(encoding="utf-8")
        assert "Mix synthetic ingredients" in text
        assets = path.parent / "assets"
        if has_image:
            [image] = assets.glob("*.jpg")
            assert image.read_bytes() == JPEG_CHUNK * 3
            assert f"](assets/{image.name})" in text
        else:
            assert not assets.exists()
            assert "image:" not in text
        assert list(path.parent.glob(".kptncook-*")) == []
        assert "kptnkey" not in text
    else:
        stored = {}
        writes = []

        def mealie(request):
            if request.url.path.endswith("/app/about"):
                return httpx.Response(200, json={"version": "v3.0.0"})
            if request.url.path.endswith("/create/zip"):
                from email.parser import BytesParser
                from email.policy import default
                from io import BytesIO

                message = BytesParser(policy=default).parsebytes(
                    b"Content-Type: "
                    + request.headers["Content-Type"].encode()
                    + b"\r\n\r\n"
                    + request.content
                )
                part = next(message.iter_parts())
                with ZipFile(BytesIO(part.get_payload(decode=True))) as archive:
                    stored.update(json.loads(archive.read("recipe.json")))
                stored["id"] = "11111111-1111-4111-8111-111111111111"
                return httpx.Response(201, json="synthetic-stew")
            if request.method == "GET":
                return httpx.Response(200, json={**stored, "slug": "synthetic-stew"})
            writes.append(request.url.path)
            if request.url.path.endswith("/assets"):
                return httpx.Response(200, json={"fileName": "step.jpg"})
            return httpx.Response(
                200,
                json={
                    **stored,
                    "slug": "synthetic-stew",
                    **json.loads(request.content),
                },
            )

        with httpx.Client(transport=httpx.MockTransport(mealie)) as api:
            client = MealieApiClient("https://mealie.example/api", client=api)
            result = client.create_recipe(
                RecipeWithImage(
                    name="Synthetic stew",
                    extras={"source": "kptncook", "kptncook_id": "a" * 24},
                    recipe_instructions=[
                        RecipeStep(
                            text="Mix synthetic ingredients",
                            image=recipe.steps[0].image,
                        )
                    ],
                )
            )
        assert result.name == "Synthetic stew"
        assert result.recipe_instructions[0].text.startswith(
            "Mix synthetic ingredients"
        )
        assert bool(writes) == has_image
        assert stored["extras"]["kptncook_id"] == "a" * 24
    assert len(requests) == 1
    assert stream.closed
    assert http.is_closed
    if timed:
        assert stream.cancelled
        assert stream.chunks >= 4
    elif failure in {"oversize", "misleading-length"}:
        assert stream.chunks == 3
    elif failure in {
        "status",
        "compressed",
        "partial-status",
        "content-range",
        "created",
        "oversize-length",
        "invalid-length",
        "empty",
    }:
        assert stream.chunks == 0
    assert list(workdir.iterdir()) == []
    assert "images.example" not in caplog.text
    assert "test-key" not in caplog.text


@pytest.mark.parametrize("outcome", ["ok", "timeout"])
def test_download_inside_running_loop(monkeypatch, outcome):
    stream = ImageStream("stalled" if outcome == "timeout" else "ok")
    client_class = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)

    monkeypatch.setattr(
        image_download.httpx,
        "AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs),
    )
    monkeypatch.setattr(image_download, "IMAGE_DOWNLOAD_DEADLINE", 0.05)

    async def notebook():
        if outcome == "timeout":
            with pytest.raises(httpx.ReadTimeout, match="total download time"):
                image_download.download_image("https://images.example/step.jpg")
        else:
            assert (
                image_download.download_image("https://images.example/step.jpg")
                == JPEG_CHUNK * 3
            )

    asyncio.run(notebook())
    assert len(requests) == 1
    assert requests[0].headers["Accept-Encoding"] == "identity"
    assert stream.closed
    assert stream.cancelled == (outcome == "timeout")


@pytest.mark.parametrize(
    "outcome", ["ok", "redirect", "status", "budget", "short", "read"]
)
def test_retirement_preserves_transfer_verdict(monkeypatch, caplog, outcome):
    requests = []
    closed_responses = []
    closed_clients = []

    class FaultyStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"1234"
            if outcome == "read":
                raise httpx.ReadError("synthetic read failure")

        async def aclose(self):
            closed_responses.append(True)
            raise httpx.CloseError("secret-close-details")

    class FaultyTransport(httpx.MockTransport):
        async def aclose(self):
            closed_clients.append(True)
            raise httpx.CloseError("secret-close-details")

    def handle(request):
        requests.append(request.url.path)
        stream = FaultyStream()
        if outcome == "redirect" and request.url.path == "/first":
            return httpx.Response(302, headers={"Location": "/second"}, stream=stream)
        if outcome == "status":
            return httpx.Response(503, stream=stream)
        headers = {"Content-Length": "5"} if outcome == "short" else {}
        return httpx.Response(200, headers=headers, stream=stream)

    transport = FaultyTransport(handle)
    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        image_download.httpx,
        "AsyncClient",
        lambda **kw: client_class(transport=transport, **kw),
    )
    if outcome == "budget":
        monkeypatch.setattr(exporter_utils, "MAX_BATCH_ASSET_BYTES", 3)

    def download():
        return image_download.download_image(
            "https://images.example/first?key=secret-image-url",
            follow_redirects=True,
            budget=exporter_utils.ExportBudget(),
        )

    if outcome == "status":
        with pytest.raises(httpx.HTTPStatusError) as caught:
            download()
        assert caught.value.response.status_code == 503
    elif outcome == "budget":
        with pytest.raises(exporter_utils.ExportLimitError, match="asset.*allowance"):
            download()
    elif outcome == "short":
        with pytest.raises(httpx.RequestError, match="not a complete representation"):
            download()
    elif outcome == "read":
        with pytest.raises(httpx.ReadError, match="synthetic read failure"):
            download()
    else:
        assert download() == b"1234"

    assert requests == (["/first", "/second"] if outcome == "redirect" else ["/first"])
    assert closed_responses == [True] * len(requests)
    assert closed_clients == [True]
    assert caplog.text.count("Could not close recipe image response") == len(requests)
    assert "Could not close recipe image client" in caplog.text
    assert "secret-close-details" not in caplog.text
    assert "secret-image-url" not in caplog.text
