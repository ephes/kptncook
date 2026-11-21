"""Selected lookups validate matches from one raw snapshot, with original positions."""

from datetime import date
from logging import Handler

import pytest

from kptncook.models import Recipe
from kptncook.repositories import RecipeInDb
from kptncook.services import repository, workflows


OID = "0123456789abcdef01234567"
UID = "Abc1234"


@pytest.fixture
def selected_store(monkeypatch, minimal):
    target = dict(minimal, _id={"$oid": OID}, uid=UID)
    broken = dict(target)
    broken.pop("steps")
    # Unrelated valid and malformed records must not be hydrated/reported.
    entries = [
        RecipeInDb(
            date=date.today(),
            data=dict(minimal if i % 2 else {}, _id={"$oid": f"{i:024x}"}),
        )
        for i in range(3000)
    ]
    entries.extend(
        RecipeInDb(date=date.today(), data=data) for data in (target, broken, target)
    )
    reads = []

    def snapshot():
        reads.append(True)
        return entries

    monkeypatch.setattr(repository, "list_repository_entries", snapshot)
    validated = []
    original = Recipe.model_validate

    def validate(data, **kwargs):
        validated.append(data)
        return original(data, **kwargs)

    monkeypatch.setattr(Recipe, "model_validate", validate)
    return entries, reads, validated


@pytest.mark.parametrize("lookup", ["service", "oid", "parsed-oid", "uid"])
def test_selected_lookup_only_validates_matches(selected_store, lookup, caplog):
    entries, reads, validated = selected_store
    if lookup == "service":
        recipes = repository.get_repository_recipe_by_oid(OID)
        assert "steps: Field required" in caplog.text
        assert "entry #" not in caplog.text
        assert len(caplog.records) == 1
    else:
        if lookup == "oid":
            result = workflows.load_recipe_from_repository_by_oid(OID)
        else:
            selector = (
                f"https://share.kptncook.com/{OID}?x=1#part"
                if lookup == "parsed-oid"
                else UID
            )
            result = workflows.load_recipe_from_repository_by_id(selector)
        recipes = result.recipes
        assert len(result.invalid_entries) == 1
        invalid = result.invalid_entries[0]
        assert (invalid.position, invalid.recipe_id, invalid.reason) == (
            3002,
            OID,
            "steps: Field required",
        )
    assert [recipe.id.oid for recipe in recipes] == [OID, OID]
    assert reads == [True]
    assert validated == [entry.data for entry in entries[-3:]]


@pytest.mark.parametrize("selector", ["f" * 24, "Xyz9876"])
def test_missing_selection_validates_nothing(selected_store, selector):
    _, reads, validated = selected_store
    result = workflows.load_recipe_from_repository_by_id(selector)
    assert result.recipes == result.invalid_entries == []
    assert reads == [True]
    assert validated == []


def test_complete_listing_still_reports_all_invalid_records(selected_store):
    _, reads, validated = selected_store
    result = repository.load_repository_recipes()
    assert len(result.recipes) == 1502
    assert len(result.invalid_entries) == 1501
    assert len(validated) == 3003
    assert reads == [True]


@pytest.mark.parametrize("selected", [False, True], ids=["list", "oid"])
def test_invalid_recipe_warning_failure_preserves_valid_results(
    monkeypatch, minimal, selected
):
    entries = [
        RecipeInDb(date=date.today(), data=data)
        for data in ({"_id": {"$oid": OID}}, dict(minimal, _id={"$oid": OID}))
    ]
    monkeypatch.setattr(repository, "list_repository_entries", lambda: entries)

    attempts = []

    class FailingHandler(Handler):
        def emit(self, record):
            attempts.append(record.getMessage())
            raise OSError("warning sink unavailable")

    handler = FailingHandler()
    repository.logger.addHandler(handler)
    try:
        recipes = (
            repository.get_repository_recipe_by_oid(OID)
            if selected
            else repository.list_repository_recipes()
        )
    finally:
        repository.logger.removeHandler(handler)

    assert [recipe.id.oid for recipe in recipes] == [OID]
    assert len(attempts) == 1
    assert attempts[0].startswith(f"Skipping invalid stored recipe {OID}:")


def test_selected_lookup_wraps_repository_failure(monkeypatch):
    def fail():
        raise repository.RepositoryServiceError("repository unavailable")

    monkeypatch.setattr(repository, "list_repository_entries", fail)
    with pytest.raises(workflows.UserFacingError, match="repository unavailable"):
        workflows.load_recipe_from_repository_by_id(UID)


def test_selected_uid_reports_malformed_identity_from_supplied_snapshot(monkeypatch):
    monkeypatch.setattr(
        repository, "list_repository_entries", lambda: pytest.fail("unexpected read")
    )
    result = repository.load_repository_recipes(
        entries=[
            RecipeInDb(date=date.today(), data={"uid": "other"}),
            RecipeInDb(date=date.today(), data={"uid": UID}),
        ],
        uid=UID,
    )
    assert result.recipes == []
    assert len(result.invalid_entries) == 1
    invalid = result.invalid_entries[0]
    assert (invalid.position, invalid.recipe_id, invalid.reason) == (
        2,
        None,
        "_id: Field required",
    )
