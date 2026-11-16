"""Delete preview identity membership scales with selection size."""

from datetime import date

import pytest

from kptncook.repositories import RecipeInDb
from kptncook.services import workflows
from kptncook.services.repository import RepositoryRecipesResult


class CountedId(str):
    comparisons = 0
    __hash__ = str.__hash__

    def __eq__(self, other):
        type(self).comparisons += 1
        return super().__eq__(other)


@pytest.mark.parametrize("count", [1000, 2000])
def test_large_selection_has_linear_identity_comparisons(monkeypatch, count):
    monkeypatch.setattr(workflows, "_repository_id_map", lambda: {})
    monkeypatch.setattr(
        workflows,
        "load_repository_recipes",
        lambda **kw: RepositoryRecipesResult([], []),
    )
    ids = [CountedId(f"missing-{index}") for index in range(count)]
    repeated = [CountedId(str(oid)) for oid in ids]
    CountedId.comparisons = 0
    result = workflows.delete_recipes_by_selection(indices=[], oids=ids + repeated)
    assert result.missing_ids == ids
    assert CountedId.comparisons <= 4 * count


def test_mixed_selection_keeps_first_occurrence_order_and_diagnostics(
    monkeypatch, minimal
):
    entries = {
        oid: RecipeInDb(date=date.today(), data={**minimal, "_id": {"$oid": oid}})
        for oid in ["a", "b", "c"]
    }
    monkeypatch.setattr(workflows, "_repository_id_map", lambda: entries)
    result = workflows.delete_recipes_by_selection(
        indices=[2, 0, 2, -1, 99], oids=["b", "c", "missing", "missing", "a"]
    )
    assert result.to_delete_ids == ["c", "a", "b"]
    assert result.missing_ids == ["missing"]
    assert result.invalid_indices == [-1, 99]
    assert list(result.expected) == ["c", "a", "b"]
