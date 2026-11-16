"""Expanded recipe leaves are admitted before any full model is built."""

from datetime import date
from unittest.mock import patch

import httpx
import pytest

from kptncook.api import KptnCookClient
from kptncook.repositories import RecipeInDb
from kptncook.services.repository import load_repository_recipes


def oversized(minimal, kind):
    data = dict(minimal)
    if kind == "wide":
        data["steps"] = [minimal["steps"][0]] * 30000
    elif kind == "deep":
        nested = None
        for _ in range(40):
            nested = {"child": nested}
        data["metadata"] = nested
    elif kind == "text":
        data["localizedTitle"] = {"de": "x" * (8 * 1024 * 1024 + 1)}
    else:
        return [minimal] * 4097
    return [data]


@pytest.mark.parametrize("kind", ["wide", "deep", "text", "count"])
@pytest.mark.parametrize("endpoint", ["today", "dailies", "search"])
def test_source_work_refused_before_model_creation(minimal, kind, endpoint):
    payload = oversized(minimal, kind)
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        client = KptnCookClient("https://api.example", api_key="synthetic", client=http)
        with patch("kptncook.api.RecipeInDb", side_effect=AssertionError("hydrated")):
            with pytest.raises(ValueError, match="Recipe model work"):
                if endpoint == "today":
                    client.list_today()
                elif endpoint == "dailies":
                    client.list_dailies()
                else:
                    client.get_by_ids([("oid", "0" * 24)])


@pytest.mark.parametrize("kind", ["wide", "deep", "text", "count"])
def test_restored_work_refused_before_full_model_creation(minimal, kind):
    entries = [
        RecipeInDb(date=date.today(), data=data) for data in oversized(minimal, kind)
    ]
    with patch(
        "kptncook.services.repository.Recipe.model_validate",
        side_effect=AssertionError("hydrated"),
    ):
        with pytest.raises(Exception, match="Recipe model work"):
            load_repository_recipes(entries=entries)


def test_valid_multi_recipe_payload_stays_complete(minimal):
    payload = [minimal, {**minimal, "_id": {"$oid": "0" * 24}}]
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        client = KptnCookClient("https://api.example", api_key="synthetic", client=http)
        entries = client.get_by_ids([("oid", "0" * 24)])
    assert [entry.data for entry in entries] == payload
    assert len(load_repository_recipes(entries=entries).recipes) == 2


def test_aggregate_work_is_shared_across_recipes(minimal, monkeypatch):
    import kptncook.recipe_admission as admission

    payload = [{**minimal, "steps": minimal["steps"] * 1000}] * 64
    admission.admit_recipes(payload)  # each recipe fits its own envelope
    monkeypatch.setattr(admission, "MAX_BATCH_WORK", 63 * 1000 * len(minimal["steps"]))
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        client = KptnCookClient("https://api.example", api_key="synthetic", client=http)
        with patch("kptncook.api.RecipeInDb", side_effect=AssertionError("hydrated")):
            with pytest.raises(ValueError, match="Recipe model work"):
                client.list_today()


def test_many_realistic_recipes_stay_admitted(full_recipe):
    entries = [
        RecipeInDb(
            date=date.today(),
            data={**full_recipe, "_id": {"$oid": f"{index:024x}"}},
        )
        for index in range(500)
    ]
    assert len(load_repository_recipes(entries=entries).recipes) == 500
    payload = [entry.data for entry in entries]
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        client = KptnCookClient("https://api.example", api_key="synthetic", client=http)
        assert len(client.get_by_ids([("oid", "0" * 24)])) == 500


def test_batch_envelope_covers_store_limit():
    import kptncook.recipe_admission as admission
    from kptncook.repositories import MAX_STORE_BYTES

    assert admission.MAX_BATCH_TEXT >= MAX_STORE_BYTES
    assert admission.MAX_BATCH_WORK >= MAX_STORE_BYTES // 2


def test_admission_exact_limits_and_cycles(monkeypatch):
    import kptncook.recipe_admission as admission

    monkeypatch.setattr(admission, "MAX_RECIPE_WORK", 3)
    monkeypatch.setattr(admission, "MAX_RECIPE_TEXT", 2)
    admission.admit_recipes([{"a": "b"}])  # dict, key, value
    admission.admit_recipes([{"a": "b"}, {"c": "d"}])  # limits are per recipe
    with pytest.raises(ValueError, match="text limit"):
        admission.admit_recipes([{"a": "bc"}])
    with pytest.raises(ValueError, match="value/key limit"):
        admission.admit_recipes([{"a": "b", "c": None}])
    monkeypatch.setattr(admission, "MAX_BATCH_WORK", 5)
    monkeypatch.setattr(admission, "MAX_BATCH_TEXT", 3)
    with pytest.raises(ValueError, match="value/key limit"):
        admission.admit_recipes([{"a": None}, {"b": None}])
    monkeypatch.setattr(admission, "MAX_BATCH_WORK", 6)
    with pytest.raises(ValueError, match="text limit"):
        admission.admit_recipes([{"a": "b"}, {"c": "d"}])
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="cyclic"):
        admission.admit_recipes([cyclic])
