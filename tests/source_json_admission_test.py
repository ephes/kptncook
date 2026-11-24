"""Source API control members are unambiguous and diagnostics stay private."""

import httpx
import pytest

from kptncook import setup
from kptncook.api import KptnCookClient
from kptncook.cli import app
from kptncook.services import workflows
from typer.testing import CliRunner


@pytest.mark.parametrize(
    "operation,body",
    [
        ("login", '{"accessToken":"private-first","accessToken":"private-last"}'),
        ("today", '[{"_id":{"$oid":"private-first","$oid":"private-last"}}]'),
        ("screen", '{"data":{"items":[],"items":["private-last"]}}'),
        ("favorites", '{"favorites":[],"private-key":NaN}'),
        ("screen", '{"private-key":Infinity}'),
        ("screen", '{"private-key":-Infinity}'),
    ],
)
def test_source_json_refused_before_interpretation(operation, body):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    ) as http:
        client = KptnCookClient(
            "https://api.example", api_key="private-key", client=http
        )
        with pytest.raises(ValueError) as caught:
            if operation == "login":
                client.get_access_token("user", "password")
            elif operation == "today":
                client.list_today()
            elif operation == "favorites":
                client.list_favorites()
            else:
                client.get_discovery_screen()
        assert "private" not in str(caught.value)
        assert caught.value.__cause__ is None
        assert not client.logged_in


def test_duplicate_token_cannot_replace_saved_token(tmp_path, monkeypatch):
    config = tmp_path / ".env"
    before = "KPTNCOOK_API_KEY=synthetic\nKPTNCOOK_ACCESS_TOKEN=previous-token\n"
    config.write_text(before)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content='{"accessToken":"first","accessToken":"last"}'
            )
        )
    ) as http:
        client = KptnCookClient("https://api.example", api_key="synthetic", client=http)
        monkeypatch.setattr(setup, "KptnCookClient", lambda **kw: client)
        monkeypatch.setattr(setup, "get_credentials", lambda *a: ("user", "password"))
        result = CliRunner().invoke(setup.cli, ["--env-path", str(config)])
    assert result.exit_code == 1
    assert config.read_text() == before


def test_valid_source_json_unchanged(minimal):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[minimal]))
    ) as http:
        recipes = KptnCookClient(
            "https://api.example", api_key="synthetic", client=http
        ).list_today()
    assert [recipe.data for recipe in recipes] == [minimal]


@pytest.mark.parametrize(
    "body, expected_lines",
    [
        pytest.param('{"private-detail":', None, id="malformed"),
        pytest.param('{"private-detail":[],"private-detail":[]}', None, id="duplicate"),
        pytest.param(
            '{"lists":[{"id":"soups","title":"Soup","listType":"curated"}],'
            '"quickSearch":["Tomato"]}',
            [
                "Discovery lists:",
                "- soups | Soup | curated",
                "Quick search:",
                "- Tomato",
            ],
            id="valid",
        ),
    ],
)
def test_discovery_screen_source_response(monkeypatch, body, expected_lines):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    ) as http:
        client = KptnCookClient("https://api.example", api_key="synthetic", client=http)
        monkeypatch.setattr(workflows, "_kptncook_client", lambda: client)
        result = CliRunner().invoke(app, ["discovery-screen"])
        if expected_lines is None:
            message = "KptnCook returned an unreadable response."
            assert result.exit_code == 1
            assert result.output.strip() == message
            with pytest.raises(workflows.UserFacingError) as caught:
                workflows.get_discovery_screen()
            assert str(caught.value) == message
        else:
            assert result.exit_code == 0, result.output
            assert result.output.splitlines() == expected_lines
