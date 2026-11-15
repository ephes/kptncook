"""One synthetic recipe crosses grouping, scaling, references and real archives."""

import gzip
import json
from zipfile import ZipFile

import httpx
import pytest
import yaml

from mealie_organizer_lookup_test import FakeMealie
from kptncook.config import settings
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.mealie import MealieApiClient, kptncook_to_mealie
from kptncook.models import Recipe
from kptncook.paprika import GeneratedData, PaprikaExporter
from kptncook.tandoor import TandoorExporter


@pytest.fixture
def grouped_reference_recipe(minimal):
    def row(oid, typ, title, quantity, measure):
        return {
            "quantity": quantity,
            "measure": measure,
            "ingredient": {
                "_id": {"$oid": oid},
                "typ": typ,
                "category": "synthetic",
                "localizedTitle": title,
                "numberTitle": title,
            },
        }

    rows = [
        row("salt", "garnish", {"de": "Salz"}, 3, "g"),
        row("oil", "basic", {"de": "Öl"}, 5, "ml"),
        row("salt", "regular", {"de": "Salz"}, 2, "g"),
        row("zest", "zesty", {"de": "Zeste"}, 7, "g"),
        row("flour", "regular", {"en": "Flour"}, 11, "g"),
    ]

    def step(text, indices):
        return {
            "title": {"de": text},
            "image": {"name": "synthetic", "url": "https://not-allowed.invalid/image"},
            "ingredients": [rows[index] for index in indices],
        }

    return Recipe.model_validate(
        {
            **minimal,
            "localizedTitle": {"de": "Synthetic grouped recipe"},
            "imageList": [],
            "ingredients": rows,
            "steps": [
                step("Salz und Öl mischen.", [0, 1]),
                step("Mehl und Zeste zugeben.", [4, 3]),
            ],
        }
    )


# An independent table: neither the grouping helper nor scaling/reference code
# under test supplies this oracle. Positions are the source rows above.
EXPECTED = {
    2: [
        ("Salz", 6, "g"),
        ("Öl", 10, "ml"),
        ("Salz", 4, "g"),
        ("Zeste", 14, "g"),
        ("Flour", 22, "g"),
    ],
    5: [
        ("Salz", 15, "g"),
        ("Öl", 25, "ml"),
        ("Salz", 10, "g"),
        ("Zeste", 35, "g"),
        ("Flour", 55, "g"),
    ],
}


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("servings", [2, 5])
def test_grouped_reference_fidelity_across_archive_and_file_exports(
    grouped_reference_recipe, monkeypatch, tmp_path, grouped, servings
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "kptncook_lang", "de")
    monkeypatch.setattr(settings, "kptncook_group_ingredients_by_typ", grouped)
    monkeypatch.setattr(
        settings,
        "kptncook_ingredient_group_labels",
        "regular:Benötigt,basic:Vorrat,garnish:Zum Schluss",
    )

    # File exporters and the native Mealie archive remain offline.
    def no_network(*args, **kwargs):
        pytest.fail("Export journey attempted a network download")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr(
        PaprikaExporter,
        "get_generated_data",
        lambda *args, **kwargs: GeneratedData(None, None, "2026-11-15", "synthetic"),
    )
    recipe = grouped_reference_recipe
    recipe.fixed_portion_count = servings
    source_rows = recipe.model_dump(mode="json")
    expected = EXPECTED[servings]
    order = [2, 4, 1, 0, 3] if grouped else [0, 1, 2, 3, 4]
    expected_rows = [expected[index] for index in order]
    labels = ["Benötigt", "Vorrat", "Zum Schluss", "Zesty"] if grouped else []
    expected_text = (
        [
            "Benötigt:",
            f"{expected[2][1]} g Salz",
            f"{expected[4][1]} g Flour",
            "Vorrat:",
            f"{expected[1][1]} ml Öl",
            "Zum Schluss:",
            f"{expected[0][1]} g Salz",
            "Zesty:",
            f"{expected[3][1]} g Zeste",
        ]
        if grouped
        else [f"{amount} {unit} {name}" for name, amount, unit in expected]
    )

    backend = FakeMealie()
    with httpx.Client(transport=httpx.MockTransport(backend.handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        client.create_recipe(kptncook_to_mealie(recipe, api_key="synthetic"))
    [mealie] = backend.imported
    ingredients = mealie["recipe_ingredient"]
    assert mealie["recipe_yield"] == f"{servings} Portionen"
    assert [
        (item["food"]["name"], item["quantity"], item["unit"]["name"])
        for item in ingredients
    ] == expected_rows
    assert [item["title"] for item in ingredients if item.get("title")] == labels
    references = {
        item["referenceId"]: (
            item["food"]["name"],
            item["quantity"],
            item["unit"]["name"],
        )
        for item in ingredients
    }
    assert len(references) == 5
    reference_order = [[2, 0, 1], [4, 3]] if grouped else [[0, 2, 1], [4, 3]]
    for step, indices in zip(
        mealie["recipe_instructions"], reference_order, strict=True
    ):
        ids = [item["referenceId"] for item in step["ingredientReferences"]]
        assert len(ids) == len(set(ids))
        assert [references[ref] for ref in ids] == [
            expected[index] for index in indices
        ]

    [filename] = TandoorExporter().export([recipe])
    with ZipFile(filename) as archive:
        tandoor = json.loads(archive.read("recipe.json"))
    assert tandoor["servings"] == servings
    headers = [item for item in tandoor["ingredients"] if item.get("is_header")]
    assert headers == [
        {"note": label, "is_header": True, "no_amount": True, "amount": 0}
        for label in labels
    ]
    real = [item for item in tandoor["ingredients"] if not item.get("is_header")]
    assert [
        (item["food"]["name"], item["amount"], item["unit"]["name"]) for item in real
    ] == expected_rows
    for step, indices in zip(tandoor["steps"], [[0, 1], [4, 3]], strict=True):
        assert [
            (item["food"]["name"], item["amount"], item["unit"]["name"])
            for item in step["ingredients"]
        ] == [expected[index] for index in indices]

    filename = PaprikaExporter().export([recipe])
    with ZipFile(filename) as archive:
        assert len(archive.namelist()) == 1
        paprika = json.loads(gzip.decompress(archive.read(archive.namelist()[0])))
    assert paprika["servings"] == str(servings)
    assert paprika["ingredients"].splitlines() == expected_text

    [path] = MarkdownExporter().export([recipe])
    frontmatter, body = path.read_text(encoding="utf-8").split("---", 2)[1:]
    assert yaml.safe_load(frontmatter)["servings"] == servings
    ingredient_section = body.split("### Zutaten", 1)[1].split("###", 1)[0]
    assert [
        line.removeprefix("- ")
        for line in ingredient_section.splitlines()
        if line.strip()
    ] == expected_text
    texts = ["Salz und Öl mischen.", "Mehl und Zeste zugeben."]
    assert [step["text"] for step in mealie["recipe_instructions"]] == texts
    assert [step["instruction"] for step in tandoor["steps"]] == texts
    assert paprika["directions"].splitlines() == texts
    for text in texts:
        assert f"- {text}" in body
    assert recipe.model_dump(mode="json") == source_rows
