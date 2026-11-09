"""Only allowlisted image hosts receive the API key or any image request."""

import gzip
import json
from zipfile import ZipFile

import httpx
import pytest

from kptncook import image_download
from kptncook.config import SettingsError, clear_settings_cache, get_settings
from kptncook.image_origin import (
    DEFAULT_IMAGE_HOSTS,
    ImageOriginError,
    is_allowed_image_url,
)
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.mealie import MealieApiClient, RecipeWithImage, kptncook_to_mealie
from kptncook.models import Image, Recipe
from kptncook.paprika import PaprikaExporter
from kptncook.tandoor import TandoorExporter

CDN = DEFAULT_IMAGE_HOSTS[0]

HOSTILE_URLS = [
    "https://outside.invalid/pixel",
    f"http://{CDN}/plain-http.jpg",
    f"https://user:pass@{CDN}/userinfo.jpg",
    f"https://{CDN}@outside.invalid/userinfo-host.jpg",
    f"https://{CDN}:8443/port.jpg",
    f"https://{CDN}.outside.invalid/suffix.jpg",
    "https://127.0.0.1/local.jpg",
    "https://localhost/local.jpg",
    "https://[::1]/local.jpg",
    "file:///etc/passwd",
    f"https://{CDN}\\@outside.invalid/backslash.jpg",
    f"https://{CDN}/space .jpg",
    f"https://{CDN}/newline\n.jpg",
    "not a url",
]


@pytest.fixture
def default_hosts_only(monkeypatch):
    # The shared test setup allows synthetic hosts; use the shipped default here.
    monkeypatch.delenv("KPTNCOOK_IMAGE_HOSTS", raising=False)
    clear_settings_cache()
    get_settings()


@pytest.fixture
def no_network(monkeypatch):
    requests = []

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=b"img")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(image_download.httpx, "AsyncClient", lambda **kw: http)
    return requests


@pytest.mark.parametrize(
    "url",
    [
        f"https://{CDN}/cover.jpg",
        f"https://{CDN}:443/cover.jpg?sig=1",
        f"https://{CDN.upper()}/cover.jpg",
        "https://images.kptncook.com/cover.jpg",
    ],
)
def test_default_cdn_is_allowed(default_hosts_only, url):
    assert is_allowed_image_url(url)


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_hostile_urls_are_refused(default_hosts_only, url):
    assert not is_allowed_image_url(url)


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_key_is_never_attached_to_a_hostile_url(default_hosts_only, url):
    image = Image(name="x.jpg", type="cover", url=url)
    with pytest.raises(ImageOriginError) as excinfo:
        image.get_image_with_api_key_url("INVENTED-KEY")
    assert "INVENTED-KEY" not in str(excinfo.value)
    assert url not in str(excinfo.value)


def test_cover_url_is_dropped_for_hostile_host(default_hosts_only, minimal, caplog):
    recipe = Recipe.model_validate(minimal)
    recipe.image_list = [
        Image(name="c.jpg", type="cover", url="https://outside.invalid/pixel")
    ]
    assert recipe.get_image_url("INVENTED-KEY") is None
    assert "outside.invalid" not in caplog.text
    assert "INVENTED-KEY" not in caplog.text


def test_configured_extra_host_is_allowed(monkeypatch):
    monkeypatch.setenv("KPTNCOOK_IMAGE_HOSTS", " Images.Example.org , cdn.example.net")
    clear_settings_cache()
    assert is_allowed_image_url("https://images.example.org/a.jpg")
    assert is_allowed_image_url("https://cdn.example.net/a.jpg")
    assert is_allowed_image_url(f"https://{CDN}/a.jpg")  # default stays allowed
    assert not is_allowed_image_url("https://example.org/a.jpg")


@pytest.mark.parametrize(
    "value",
    [
        "https://images.example.org",
        "images.example.org:8443",
        "images.example.org/path",
        "127.0.0.1",
        "localhost",
        "user@images.example.org",
    ],
)
def test_invalid_configured_hosts_are_refused(monkeypatch, value):
    monkeypatch.setenv("KPTNCOOK_IMAGE_HOSTS", value)
    clear_settings_cache()
    with pytest.raises(SettingsError):
        get_settings()


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_download_refuses_hostile_url_without_request(
    default_hosts_only, no_network, url
):
    with pytest.raises(httpx.RequestError) as excinfo:
        image_download.download_image(url, follow_redirects=True)
    assert no_network == []
    assert url not in str(excinfo.value)


def _redirecting_transport(monkeypatch, location):
    requests = []

    def handle(request):
        requests.append(str(request.url))
        if request.url.path == "/start.jpg":
            return httpx.Response(302, headers={"Location": location})
        return httpx.Response(200, content=b"img")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(image_download.httpx, "AsyncClient", lambda **kw: http)
    return requests


@pytest.mark.parametrize(
    "location",
    [
        "https://outside.invalid/escape.jpg",
        f"http://{CDN}/downgrade.jpg",
        "https://127.0.0.1/local.jpg",
        f"https://{CDN}:8443/port.jpg",
    ],
)
def test_redirect_escape_is_refused_before_request(
    default_hosts_only, monkeypatch, location
):
    requests = _redirecting_transport(monkeypatch, location)
    with pytest.raises(httpx.RequestError):
        image_download.download_image(
            f"https://{CDN}/start.jpg?kptnkey=INVENTED", follow_redirects=True
        )
    assert requests == [f"https://{CDN}/start.jpg?kptnkey=INVENTED"]


def test_allowed_redirects_are_followed(default_hosts_only, monkeypatch):
    requests = _redirecting_transport(monkeypatch, "/final.jpg")
    body = image_download.download_image(
        f"https://{CDN}/start.jpg", follow_redirects=True
    )
    assert body == b"img"
    assert requests == [f"https://{CDN}/start.jpg", f"https://{CDN}/final.jpg"]


def test_redirects_are_not_followed_unless_requested(default_hosts_only, monkeypatch):
    requests = _redirecting_transport(monkeypatch, "/final.jpg")
    with pytest.raises(httpx.HTTPStatusError):
        image_download.download_image(f"https://{CDN}/start.jpg")
    assert requests == [f"https://{CDN}/start.jpg"]


def test_redirect_loop_is_bounded(default_hosts_only, monkeypatch):
    requests = _redirecting_transport(monkeypatch, "/start.jpg")
    with pytest.raises(httpx.RequestError, match="redirect limit"):
        image_download.download_image(f"https://{CDN}/start.jpg", follow_redirects=True)
    assert len(requests) == image_download.MAX_IMAGE_REDIRECTS + 1


def _hostile_recipe(minimal):
    recipe = Recipe.model_validate(minimal)
    recipe.id.oid = "b" * 24
    recipe.localized_title.de = "Hostile images"
    recipe.image_list = [
        Image(name="c.jpg", type="cover", url="https://outside.invalid/cover.jpg")
    ]
    recipe.steps[0].title.de = "Keep this step text"
    recipe.steps[0].image = Image(
        name="s.jpg", type="step", url="https://127.0.0.1/step.jpg"
    )
    return recipe


@pytest.mark.parametrize("exporter", ["paprika", "tandoor", "markdown"])
def test_exports_keep_text_and_make_no_hostile_request(
    default_hosts_only, no_network, minimal, monkeypatch, tmp_path, exporter, caplog
):
    monkeypatch.chdir(tmp_path)
    recipe = _hostile_recipe(minimal)
    if exporter == "paprika":
        filename = PaprikaExporter().export([recipe])
        with ZipFile(filename) as archive:
            payload = json.loads(gzip.decompress(archive.read(archive.namelist()[0])))
        assert payload["directions"] == "Keep this step text\n"
        assert not payload["photo_data"]
    elif exporter == "tandoor":
        filename = TandoorExporter().export_recipe(recipe)
        with ZipFile(filename) as archive:
            payload = json.loads(archive.read("recipe.json"))
            assert "image.jpg" not in archive.namelist()
        assert payload["steps"][0]["instruction"] == "Keep this step text"
    else:
        [path] = MarkdownExporter().export([recipe])
        assert "Keep this step text" in path.read_text(encoding="utf-8")
    assert no_network == []
    assert "outside.invalid" not in caplog.text
    assert "127.0.0.1" not in caplog.text


def test_mealie_conversion_drops_hostile_images(default_hosts_only, minimal):
    recipe = _hostile_recipe(minimal)
    converted = kptncook_to_mealie(recipe, api_key="INVENTED-KEY")
    assert converted.image_url is None
    assert converted.recipe_instructions[0].image is None
    assert converted.recipe_instructions[0].text == "Keep this step text"
    assert "INVENTED-KEY" not in converted.model_dump_json()


def test_mealie_never_asks_server_to_scrape_hostile_url(default_hosts_only):
    client = MealieApiClient("http://mealie.local/api")
    client.post = lambda *a, **kw: pytest.fail("Hostile cover URL sent to Mealie")
    recipe = RecipeWithImage(
        name="Hostile", image_url="https://outside.invalid/pixel?kptnkey=INVENTED"
    )
    client._scrape_image_for_recipe(recipe, "hostile")


def test_mealie_allowed_cover_is_uploaded_not_scraped(default_hosts_only, no_network):
    client = MealieApiClient("http://mealie.local/api")
    client.post = lambda *a, **kw: pytest.fail("Mealie must not fetch the URL")
    uploads = []

    def fake_put(path, **kwargs):
        uploads.append((path, kwargs["files"]["image"][1]))
        return httpx.Response(
            200, request=httpx.Request("PUT", "http://mealie.local/api" + path)
        )

    client.put = fake_put
    url = f"https://{CDN}/cover.jpg?kptnkey=KEY"
    client._scrape_image_for_recipe(RecipeWithImage(name="Ok", image_url=url), "ok")
    assert no_network == [url]
    assert uploads == [("/recipes/ok/image", b"img")]


def test_mealie_cover_redirect_escape_is_never_followed(
    default_hosts_only, monkeypatch
):
    requests = _redirecting_transport(monkeypatch, "https://outside.invalid/x.jpg")
    client = MealieApiClient("http://mealie.local/api")
    client.post = lambda *a, **kw: pytest.fail("Mealie must not fetch the URL")
    client.put = lambda *a, **kw: pytest.fail("No cover may be uploaded")
    url = f"https://{CDN}/start.jpg?kptnkey=KEY"
    with pytest.raises(httpx.RequestError):
        client._scrape_image_for_recipe(
            RecipeWithImage(name="Redirect", image_url=url), "redirect"
        )
    assert requests == [url]


@pytest.mark.parametrize(
    "value",
    ["127.0.0.0x1", "0x7f.0.0.1", "0177.0.0.1", "127.1", "1.2.3.0x4"],
)
def test_alternate_numeric_address_spellings_are_refused(monkeypatch, value):
    monkeypatch.setenv("KPTNCOOK_IMAGE_HOSTS", value)
    clear_settings_cache()
    with pytest.raises(SettingsError):
        get_settings()


@pytest.mark.parametrize("failure", ["status", "transport"])
def test_failed_cover_download_logs_neither_url_nor_key(
    default_hosts_only, monkeypatch, caplog, failure
):
    def handle(request):
        if failure == "transport":
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)
        return httpx.Response(403)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(image_download.httpx, "AsyncClient", lambda **kw: http)

    def mealie_server(request):
        path = request.url.path
        if path == "/api/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        if path == "/api/recipes/create/zip":
            return httpx.Response(201, json="cover-test")
        if request.method == "GET" and path == "/api/recipes/cover-test":
            return httpx.Response(
                200,
                json={
                    "slug": "cover-test",
                    "extras": {"source": "kptncook", "kptncook_id": "id"},
                },
            )
        if request.method == "GET":
            return httpx.Response(200, json={"items": [], "total_pages": 0})
        pytest.fail(f"Unexpected {request.method} {path}")

    url = f"https://{CDN}/cover.jpg?kptnkey=SYNTHETIC-KEY"
    with httpx.Client(transport=httpx.MockTransport(mealie_server)) as mealie_http:
        client = MealieApiClient("http://mealie.local/api", client=mealie_http)
        with caplog.at_level("DEBUG"):
            created = client.create_recipe(
                RecipeWithImage(
                    name="Cover",
                    image_url=url,
                    extras={"source": "kptncook", "kptncook_id": "id"},
                )
            )
    assert created.slug == "cover-test"
    # kptncook's own records (httpx's INFO request lines are a separate,
    # opt-in library log and are out of scope here).
    ours = "\n".join(
        record.getMessage() + (str(record.exc_info) if record.exc_info else "")
        for record in caplog.records
        if record.name.startswith("kptncook")
    )
    assert "Skipping cover image for recipe cover-test" in ours
    assert "SYNTHETIC-KEY" not in ours
    assert CDN not in ours
    assert not any(
        record.exc_info
        for record in caplog.records
        if record.name.startswith("kptncook")
    )
