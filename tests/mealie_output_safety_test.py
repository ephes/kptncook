"""Untrusted asset names remain URL data and diagnostic logs omit secrets."""

import logging
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import pytest

from kptncook.mealie import MealieApiClient, kptncook_to_mealie
from kptncook.models import Recipe


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@pytest.mark.parametrize(
    "filename",
    [
        "step.jpg",
        'a" onerror="alert(1)',
        "<script>x</script>",
        "a&b c.jpg",
        "folder/../../step.jpg",
        "a?#.jpg",
    ],
)
def test_asset_filename_cannot_escape_its_path_or_attribute(filename):
    text = "<strong>Mix & bake</strong>"
    rendered = MealieApiClient._build_recipestep_text("synthetic-id", text, filename)
    parser = Elements()
    parser.feed(rendered)
    assert [tag for tag, attrs in parser.elements] == ["strong", "img"]
    attrs = parser.elements[-1][1]
    assert set(attrs) == {"src", "height", "width"}
    assert attrs["src"] == "/api/media/recipes/synthetic-id/assets/" + quote(
        filename, safe=""
    )
    assert urlsplit(attrs["src"]).query == ""
    assert urlsplit(attrs["src"]).fragment == ""
    assert rendered.startswith(text + " ")


def test_recipe_id_is_also_one_media_path_component():
    parser = Elements()
    parser.feed(MealieApiClient._build_recipestep_text('id/with"<&', "Mix", "step.jpg"))
    assert (
        parser.elements[0][1]["src"]
        == "/api/media/recipes/id%2Fwith%22%3C%26/assets/step.jpg"
    )


def test_conversion_debug_logs_omit_cover_and_nested_step_credentials(minimal, caplog):
    sentinel = "synthetic-API-KEY-secret-sentinel"
    recipe = Recipe.model_validate(minimal)
    with caplog.at_level(logging.DEBUG, logger="kptncook.mealie"):
        converted = kptncook_to_mealie(recipe, api_key=sentinel)
    # Establish both sensitive sources exist, so absence in logs proves redaction.
    assert sentinel in converted.image_url
    assert sentinel in converted.recipe_instructions[0].image.url
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert caplog.records
    assert sentinel not in messages
    assert "kptnkey=" not in messages
    assert converted.image_url not in messages
    assert converted.recipe_instructions[0].image.url not in messages


@pytest.mark.parametrize("component", ["", ".", ".."])
def test_empty_or_dot_media_segments_are_refused(component):
    with pytest.raises(ValueError, match="media path"):
        MealieApiClient._build_recipestep_text("synthetic-id", "Mix", component)
    with pytest.raises(ValueError, match="media path"):
        MealieApiClient._build_recipestep_text(component, "Mix", "step.jpg")
