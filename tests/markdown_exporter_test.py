import pytest

from kptncook.config import get_settings, settings
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.models import Recipe


def build_recipe(**overrides) -> Recipe:
    """Build a minimal valid Recipe, applying top-level key overrides."""
    payload = {
        "_id": {"$oid": "1"},
        "uid": "abc123",
        "localizedTitle": {"de": "Test Rezept", "en": "Test Recipe"},
        "authorComment": "",
        "preparationTime": 5,
        "cookingTime": 10,
        "recipeNutrition": {
            "calories": 100,
            "protein": 0,
            "fat": 0,
            "carbohydrate": 0,
        },
        "activeTags": [],
        "steps": [
            {
                "title": {"de": "Alles parat?", "en": "All set?"},
                "image": {"name": "img0.jpg", "url": "http://img0", "type": "step"},
            },
            {
                "title": {"de": "Mehl abwiegen.", "en": "Weigh the flour."},
                "image": {"name": "img1.jpg", "url": "http://img1", "type": "step"},
            },
        ],
        "imageList": [],
        "ingredients": [],
    }
    payload.update(overrides)
    return Recipe.model_validate(payload)


def ingredient(quantity, measure, name_de):
    return {
        "quantity": quantity,
        "measure": measure,
        "ingredient": {
            "typ": "regular",
            "uncountableTitle": {"de": name_de},
            "category": "baking",
        },
    }


@pytest.fixture
def exporter():
    # conftest's autouse fixture sets KPTNCOOK_API_KEY / KPTNCOOK_HOME env vars;
    # loading settings here populates the cache the proxy reads from.
    get_settings()
    return MarkdownExporter()


def test_replace_timers_in_step(exporter):
    recipe = build_recipe(
        steps=[
            {
                "title": {"de": "Cook for <timer> then again for <timer>"},
                "image": {"name": "img.jpg", "url": "http://img", "type": "step"},
                "timers": [{"minOrExact": 5}, {"minOrExact": 10}],
            }
        ]
    )
    md = exporter.render_recipe(recipe)
    assert "Cook for 5m then again for 10m" in md


def test_prep_step_is_skipped(exporter):
    md = exporter.render_recipe(build_recipe())
    assert "Alles parat?" not in md
    assert "- Mehl abwiegen." in md


def test_quantity_is_not_scaled_by_servings(exporter):
    recipe = build_recipe(
        fixedPortionCount=4,
        ingredients=[ingredient(40.0, "g", "Mehl")],
    )
    md = exporter.render_recipe(recipe)
    # quantity is emitted as-is (40), not multiplied by the 4 servings
    assert "- 40 g Mehl" in md
    assert "160" not in md


def test_cooktime_omitted_when_missing(exporter):
    recipe = build_recipe()
    recipe.cooking_time = None
    md = exporter.render_recipe(recipe)
    assert "prepTime: 5m" in md
    assert "cookTime" not in md


def test_section_headers_follow_language(exporter, monkeypatch):
    monkeypatch.setattr(settings, "kptncook_lang", "de")
    assert "### Zutaten" in exporter.render_recipe(build_recipe())

    monkeypatch.setattr(settings, "kptncook_lang", "en")
    md_en = exporter.render_recipe(build_recipe())
    assert "### Ingredients" in md_en
    assert "### Instructions" in md_en

    # unknown language falls back to English
    monkeypatch.setattr(settings, "kptncook_lang", "xx")
    assert "### Ingredients" in exporter.render_recipe(build_recipe())


def test_tag_filtering_and_transforms(exporter):
    recipe = build_recipe(
        rtype="Vegan",
        activeTags=[
            "spring",
            "comfort_foot",
            "diet_vegetarian",
            "main_ingredient_pasta",
            "cooking_time_under_20",
            "kid_friendly",
        ],
    )
    md = exporter.render_recipe(recipe)
    assert "  - vegan" in md
    assert "  - season/spring" in md
    assert "  - comfort_food" in md  # upstream typo corrected
    assert "simple: true" in md
    # filtered-out tags
    assert "diet_vegetarian" not in md
    assert "main_ingredient_pasta" not in md
    assert "kid_friendly" not in md


def test_link_falls_back_to_recipe_id_when_uid_missing(exporter):
    recipe = build_recipe(uid=None, _id={"$oid": "deadbeef"})
    md = exporter.render_recipe(recipe)
    assert "link: https://mobile.kptncook.com/recipe/pinterest/deadbeef" in md
    assert "/None" not in md


def test_export_writes_one_file_per_recipe():
    get_settings()
    recipe = build_recipe()

    written = MarkdownExporter().export([recipe])

    assert len(written) == 1
    out_path = written[0]
    assert out_path.parent == settings.root / "export_md"
    assert out_path.suffix == ".md"
    assert out_path.exists()
    assert "### Zutaten" in out_path.read_text(encoding="utf-8")


def test_export_disambiguates_duplicate_titles():
    get_settings()
    # same title, different ids -> must not overwrite each other
    first = build_recipe(_id={"$oid": "1"})
    second = build_recipe(_id={"$oid": "2"})

    written = MarkdownExporter().export([first, second])

    assert len(written) == 2
    assert written[0] != written[1]
    assert all(path.exists() for path in written)


def test_export_handles_secondary_filename_collisions():
    get_settings()
    # "Title" + id "2" would naively become "Title-2", colliding with the first
    # recipe's own title stem -> allocation must keep going until unique
    r1 = build_recipe(localizedTitle={"de": "Title-2"}, _id={"$oid": "1"})
    r2 = build_recipe(localizedTitle={"de": "Title"}, _id={"$oid": "9"})
    r3 = build_recipe(localizedTitle={"de": "Title"}, _id={"$oid": "2"})

    written = MarkdownExporter().export([r1, r2, r3])

    assert len({str(path) for path in written}) == 3
    assert all(path.exists() for path in written)
