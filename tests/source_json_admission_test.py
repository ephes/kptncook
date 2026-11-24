"""Source API control members are unambiguous and diagnostics stay private."""

import httpx
import pytest
from typer.testing import CliRunner

from kptncook import setup
from kptncook.api import KptnCookClient
from kptncook.cli import app
from kptncook.services import workflows


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


@pytest.mark.parametrize(
    "body, expected_row",
    [
        pytest.param("[]", "", id="empty"),
        pytest.param('{"ingredients":[],"items":[null]}', "", id="wrapper-precedence"),
        pytest.param('[{"id":"1","name":"Salt"}]', "- 1 | Salt", id="bare-list"),
        pytest.param('{"ingredients":[{"id":"2"}]}', "- 2 | -", id="ingredients"),
        pytest.param('{"items":[{"id":"3"}]}', "- 3 | -", id="items"),
        pytest.param('{"data":[{"id":"4"}]}', "- 4 | -", id="data"),
        pytest.param('[{"id":"hidden"},null]', None, id="mixed-list"),
        pytest.param('{"items":[{"id":"hidden"},42]}', None, id="mixed-wrapper"),
        pytest.param('{"unexpected":[]}', None, id="unknown-wrapper"),
        pytest.param('{"items":{}}', None, id="wrong-wrapper-type"),
        pytest.param("null", None, id="wrong-root-type"),
        pytest.param('{"items":', None, id="malformed-json"),
        pytest.param('{"items":[],"items":[]}', None, id="duplicate-member"),
    ],
)
def test_popular_ingredients_source_response(monkeypatch, body, expected_row):
    monkeypatch.setenv("KPTNCOOK_ACCESS_TOKEN", "synthetic-token")

    def respond(request):
        assert request.url.path == "/ingredients/popular"
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = KptnCookClient(
            "https://api.example",
            api_key="synthetic",
            access_token="synthetic-token",
            client=http,
        )
        monkeypatch.setattr(workflows, "_kptncook_client", lambda: client)
        result = CliRunner().invoke(app, ["ingredients-popular"])
        if expected_row is None:
            with pytest.raises(workflows.UserFacingError) as caught:
                workflows.list_popular_ingredients()
            assert str(caught.value) == "KptnCook returned an unreadable response."

    if expected_row is None:
        assert result.exit_code == 1
        assert result.output.strip() == "KptnCook returned an unreadable response."
    else:
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == (
            ["Popular ingredients:", expected_row]
            if expected_row
            else ["No popular ingredients found."]
        )
