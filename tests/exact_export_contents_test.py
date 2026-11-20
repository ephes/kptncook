"""Offline payload fidelity and exact legacy archive membership."""

import gzip
from zipfile import ZipFile

import pytest

from kptncook.config import settings
from kptncook.mealie import kptncook_to_mealie
from kptncook.models import Recipe
from kptncook.paprika import PaprikaExporter


@pytest.mark.parametrize(
    "title,food,note",
    [
        ("tomatoes", "tomatoes", None),
        ("tomatoes, peeled", "tomatoes", "peeled"),
        (" tomatoes , peeled, roughly chopped ", "tomatoes", "peeled, roughly chopped"),
        (
            "tomatoes, peeled,  roughly chopped, drained",
            "tomatoes",
            "peeled,  roughly chopped, drained",
        ),
    ],
)
@pytest.mark.parametrize("grouped", [False, True])
def test_mealie_payload_retains_complete_preparation_note(
    minimal, monkeypatch, title, food, note, grouped
):
    monkeypatch.setattr(settings, "kptncook_group_ingredients_by_typ", grouped)
    monkeypatch.setattr(settings, "kptncook_ingredient_group_labels", "regular:Need")
    minimal["ingredients"] = [
        {
            "quantity": 1,
            "measure": "g",
            "ingredient": {
                "typ": "regular",
                "category": "vegetables",
                "localizedTitle": {"en": title},
            },
        }
    ]
    recipe = Recipe.model_validate(minimal)
    payload = kptncook_to_mealie(recipe, api_key="synthetic").model_dump(mode="json")
    [ingredient] = payload["recipe_ingredient"]
    assert ingredient["food"]["name"] == food
    assert ingredient["note"] == note
    assert ingredient["title"] == ("Need" if grouped else None)


def test_legacy_paprika_archive_contains_only_requested_members(tmp_path):
    exporter = PaprikaExporter()
    previous = tmp_path / "unrelated.paprikarecipe"
    previous.write_bytes(b"keep unrelated bytes")
    for oid in ("first", "second"):
        result = exporter.save_recipes(
            {oid: '{"synthetic":true}'}, f"{oid}.zip", str(tmp_path)
        )
        with ZipFile(result) as archive:
            assert archive.namelist() == [f"recipe_{oid}.paprikarecipe"]
            sidecar = tmp_path / archive.namelist()[0]
            assert sidecar.read_bytes() == archive.read(sidecar.name)
            assert (
                gzip.decompress(archive.read(archive.namelist()[0]))
                == b'{"synthetic":true}'
            )
        assert previous.read_bytes() == b"keep unrelated bytes"
    assert (tmp_path / "recipe_first.paprikarecipe").exists()
    result = exporter.save_recipes({}, "empty.zip", str(tmp_path))
    with ZipFile(result) as archive:
        assert archive.namelist() == []


@pytest.mark.parametrize("failure", ["second recipe", "archive"])
def test_legacy_paprika_staging_failure_preserves_existing_bundle(
    tmp_path, monkeypatch, failure
):
    exporter = PaprikaExporter()
    filename = "recipes.paprikarecipes"
    exporter.save_recipes({"first": '{"old":true}'}, filename, str(tmp_path))
    original = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    if failure == "second recipe":
        original_write = gzip.GzipFile.write

        def fail_second(stream, data):
            if data == b'{"synthetic":true}':
                original_write(stream, data[:4])
                raise OSError("injected write failure")
            return original_write(stream, data)

        monkeypatch.setattr(gzip.GzipFile, "write", fail_second)
    else:

        def fail_zip(path, entries):
            path.write_bytes(b"partial archive")
            raise OSError("injected write failure")

        monkeypatch.setattr("kptncook.paprika.write_zip", fail_zip)

    with pytest.raises(OSError, match="injected write failure"):
        exporter.save_recipes(
            {"first": '{"new":true}', "second": '{"synthetic":true}'},
            filename,
            str(tmp_path),
        )

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == original
