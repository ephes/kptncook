"""httpx and httpcore log records never carry API keys, tokens or credentials."""

import logging
import subprocess
import sys

import httpx
import pytest

from kptncook import log_redaction
from kptncook.api import KptnCookClient
from kptncook.http_client import BaseHttpClient

SECRET = "synthetic-secret-value"


def _client(requests: list[httpx.Request]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json=[])
        )
    )


def test_httpx_request_log_redacts_query_secrets(caplog):
    requests: list[httpx.Request] = []
    with caplog.at_level(logging.DEBUG), _client(requests) as http:
        client = BaseHttpClient("https://api.example/", client=http)
        client.get(
            "recipes",
            params={"kptnkey": SECRET, "token": SECRET, "lang": "de"},
        )
        http.get(f"https://user:{SECRET}@images.example/a.jpg?kptnkey={SECRET}")

    # The request itself is unchanged; only the log record is redacted.
    assert requests[0].url.params["kptnkey"] == SECRET
    records = [r for r in caplog.records if r.name == "httpx"]
    assert len(records) == 2
    text = caplog.text
    assert SECRET not in text
    assert "kptnkey=[REDACTED]" in text
    assert "token=[REDACTED]" in text
    assert "lang=de" in text
    assert "https://[REDACTED]@images.example/a.jpg" in text


def test_query_secret_with_quote_and_semicolon_is_fully_redacted(caplog):
    awkward = "abc'def;ghi"
    with caplog.at_level(logging.INFO, logger="httpx"), _client([]) as http:
        BaseHttpClient("https://api.example/", client=http).get(
            f"recipes/search?kptnkey={awkward}&lang=de"
        )

    assert "def" not in caplog.text
    assert "ghi" not in caplog.text
    assert "kptnkey=[REDACTED]&lang=de" in caplog.text


def test_importing_any_kptncook_module_installs_the_filter():
    # image_download talks to httpx directly; the package import covers it.
    code = (
        "import logging, kptncook.image_download\n"
        "from kptncook.log_redaction import HTTP_LOGGERS, SecretRedactingFilter\n"
        "assert all(any(isinstance(f, SecretRedactingFilter) for f in "
        "logging.getLogger(n).filters) for n in HTTP_LOGGERS)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_kptncook_api_request_log_has_no_api_key(caplog):
    requests: list[httpx.Request] = []
    with caplog.at_level(logging.INFO, logger="httpx"), _client(requests) as http:
        client = KptnCookClient(
            "https://mobile.kptncook.example/", api_key=SECRET, client=http
        )
        client.list_today()

    assert requests
    assert SECRET in str(requests[0].url)
    assert "HTTP Request:" in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize(
    "message",
    [
        "send_request_headers.started request=<Request [b'GET']> "
        f"headers=[(b'Authorization', b'Bearer {SECRET}'), (b'kptnkey', b'{SECRET}')]",
        "receive_response_headers.complete return_value=(b'HTTP/1.1', 200, b'OK', "
        f"[(b'Set-Cookie', b'session={SECRET}; Path=/'), (b'Content-Type', b'text/html')])",
        f"headers={{'Token': '{SECRET}', 'Accept': 'application/json'}}",
        f"Headers({{'cookie': 'a={SECRET}', 'x-api-key': \"{SECRET}\"}})",
    ],
)
def test_httpcore_trace_header_values_are_redacted(caplog, message):
    with caplog.at_level(logging.DEBUG, logger="httpcore.http11"):
        logging.getLogger("httpcore.http11").debug("%s", message)

    assert SECRET not in caplog.text
    assert log_redaction.REDACTED in caplog.text


def test_non_secret_records_are_left_alone(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info(
            "HTTP Request: %s %s", "GET", "https://api.example/recipes?monkey=1&lang=de"
        )

    record = caplog.records[-1]
    assert record.args == ("GET", "https://api.example/recipes?monkey=1&lang=de")


def test_install_is_idempotent():
    log_redaction.install()
    log_redaction.install()
    for name in log_redaction.HTTP_LOGGERS:
        filters = logging.getLogger(name).filters
        assert (
            sum(isinstance(f, log_redaction.SecretRedactingFilter) for f in filters)
            == 1
        )
