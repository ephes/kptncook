"""Organizer lookups refuse ambiguity and read each inventory once per sync."""

import json
from collections import Counter
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest

import kptncook.mealie as mealie
from kptncook.mealie import (
    MealieApiClient,
    MealieOrganizerAmbiguityError,
    RecipeFood,
    RecipeIngredient,
    RecipeTag,
    RecipeUnit,
    RecipeWithImage,
)
from kptncook.models import Recipe
from kptncook.services import workflows
from kptncook.services.repository import RepositoryRecipesResult

ORGANIZERS = ("units", "foods", "organizers/tags")


class FakeMealie:
    """A synthetic Mealie that counts requests and inventory rows served."""

    def __init__(self, organizers=None):
        self.organizers = {
            name: list((organizers or {}).get(name, [])) for name in ORGANIZERS
        }
        self.recipes = {}
        self.requests = Counter()
        self.rows_listed = Counter()
        self.writes = []
        self.imported = []

    def handle(self, request):
        path = request.url.path.removeprefix("/api")
        endpoint = path.lstrip("/")
        self.requests[(request.method, endpoint)] += 1
        if path == "/app/about":
            return httpx.Response(200, json={"version": "v3.28.0"})
        if request.method == "GET" and endpoint in ORGANIZERS + ("recipes",):
            rows = (
                list(self.recipes.values())
                if endpoint == "recipes"
                else self.organizers[endpoint]
            )
            self.rows_listed[endpoint] += len(rows)
            return httpx.Response(200, json={"items": rows, "total_pages": 1})
        if request.method == "POST" and endpoint in ORGANIZERS:
            self.writes.append(endpoint)
            item = json.loads(request.content)
            created = {"id": str(uuid4()), "name": item["name"]}
            self.organizers[endpoint].append(created)
            return httpx.Response(201, json=created)
        if request.method == "POST" and path == "/recipes/create/zip":
            self.writes.append("archive")
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: "
                + request.headers["Content-Type"].encode()
                + b"\r\n\r\n"
                + request.content
            )
            part = next(message.iter_parts())
            with ZipFile(BytesIO(part.get_payload(decode=True))) as archive:
                payload = json.loads(archive.read("recipe.json"))
            self.imported.append(payload)
            slug = f"recipe-{len(self.recipes) + 1}"
            self.recipes[slug] = {
                "id": str(uuid4()),
                "slug": slug,
                "name": payload["name"],
                "extras": payload["extras"],
            }
            return httpx.Response(201, json=slug)
        if request.method == "GET" and path.startswith("/recipes/"):
            return httpx.Response(200, json=self.recipes[path.rsplit("/", 1)[-1]])
        pytest.fail(f"Unexpected {request.method} {path}")


def _recipe(tags=(), foods=(), units=()):
    ingredients = [RecipeIngredient(food=RecipeFood(name=f), unit=None) for f in foods]
    ingredients += [RecipeIngredient(food=None, unit=RecipeUnit(name=u)) for u in units]
    return RecipeWithImage(
        name="Synthetic",
        tags=[RecipeTag(name=t) for t in tags],
        recipe_ingredient=ingredients,
        extras={"source": "kptncook", "kptncook_id": "synthetic"},
    )


@pytest.mark.parametrize("order", [("Vegan", "vegan"), ("vegan", "Vegan")])
def test_ambiguous_existing_tags_refuse_before_any_write(order):
    server = FakeMealie(
        {"organizers/tags": [{"id": str(uuid4()), "name": name} for name in order]}
    )
    with httpx.Client(transport=httpx.MockTransport(server.handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        # Units and foods would need creating; the tag ambiguity must stop that.
        recipe = _recipe(tags=["vegan"], foods=["New food"], units=["New unit"])
        with pytest.raises(MealieOrganizerAmbiguityError, match="tag 'vegan'"):
            client.create_recipe(recipe)
    assert server.writes == []


def test_ambiguous_existing_foods_refuse_before_any_write():
    server = FakeMealie(
        {"foods": [{"id": str(uuid4()), "name": "Salt"} for _ in range(2)]}
    )
    with httpx.Client(transport=httpx.MockTransport(server.handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        with pytest.raises(MealieOrganizerAmbiguityError, match="food 'Salt'"):
            client.create_recipe(_recipe(foods=["Salt"], tags=["new-tag"]))
    assert server.writes == []


def test_unrelated_ambiguity_does_not_block_other_names():
    vegan_ids = [str(uuid4()), str(uuid4())]
    dessert_id = str(uuid4())
    server = FakeMealie(
        {
            "organizers/tags": [
                {"id": vegan_ids[0], "name": "Vegan"},
                {"id": vegan_ids[1], "name": "vegan"},
                {"id": dessert_id, "name": "Dessert"},
            ]
        }
    )
    with httpx.Client(transport=httpx.MockTransport(server.handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        client.create_recipe(_recipe(tags=["dessert"]))
    [payload] = server.imported
    assert [tag["id"] for tag in payload["tags"]] == [dessert_id]
    assert server.writes == ["archive"]


def test_exact_and_normalized_matches_keep_existing_ids():
    tag_id, food_id, other_food_id = str(uuid4()), str(uuid4()), str(uuid4())
    server = FakeMealie(
        {
            "organizers/tags": [{"id": tag_id, "name": "Dessert"}],
            # Foods match exactly, so different casing is a different food.
            "foods": [
                {"id": food_id, "name": "Salt"},
                {"id": other_food_id, "name": "salt"},
            ],
        }
    )
    with httpx.Client(transport=httpx.MockTransport(server.handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        client.create_recipe(_recipe(tags=["dessert", "DESSERT"], foods=["Salt"]))
    [payload] = server.imported
    assert {tag["id"] for tag in payload["tags"]} == {tag_id}
    assert payload["recipe_ingredient"][0]["food"]["id"] == food_id
    assert server.writes == ["archive"]


def _local_recipes(minimal, count):
    recipes = []
    for number in range(count):
        data = json.loads(json.dumps(minimal))
        data["_id"]["$oid"] = f"{number:024x}"
        data["localizedTitle"]["de"] = f"Recipe {number}"
        recipes.append(Recipe.model_validate(data))
    return recipes


def _converted(recipe):
    number = int(recipe.id.oid, 16)
    # Every recipe adds a new food and reuses all earlier ones.
    return _recipe(
        tags=["kptncook", f"tag-{number % 3}"],
        foods=[f"Food {n}" for n in range(number + 1)],
        units=["g"],
    ).model_copy(
        update={
            "name": f"Recipe {number}",
            "extras": {"source": "kptncook", "kptncook_id": recipe.id.oid},
        }
    )


def _sync(monkeypatch, server, recipes):
    http = httpx.Client(transport=httpx.MockTransport(server.handle))
    monkeypatch.setattr(
        workflows,
        "get_mealie_client",
        lambda: MealieApiClient("https://mealie.example/api", client=http),
    )
    monkeypatch.setattr(
        workflows,
        "load_kptncook_recipes_from_repository",
        lambda: RepositoryRecipesResult(recipes, []),
    )
    monkeypatch.setattr(workflows, "kptncook_to_mealie", _converted)
    return workflows.sync_with_mealie_result()


@pytest.mark.parametrize("count", [10, 100])
def test_sync_reads_each_organizer_inventory_once(monkeypatch, minimal, count):
    server = FakeMealie()
    result = _sync(monkeypatch, server, _local_recipes(minimal, count))

    assert result.created_count == count
    assert result.failed_recipes == []
    for endpoint in ORGANIZERS:
        assert server.requests[("GET", endpoint)] == 1
    # Only required creates: one per distinct food, tag and unit.
    assert server.requests[("POST", "foods")] == count
    assert server.requests[("POST", "organizers/tags")] == 1 + min(count, 3)
    assert server.requests[("POST", "units")] == 1
    # Imported recipes reference the IDs Mealie assigned.
    food_ids = {food["name"]: food["id"] for food in server.organizers["foods"]}
    last = server.imported[-1]
    assert [i["food"]["id"] for i in last["recipe_ingredient"] if i.get("food")] == [
        food_ids[f"Food {n}"] for n in range(count)
    ]


def test_next_sync_rereads_remote_organizers(monkeypatch, minimal):
    server = FakeMealie()
    recipes = _local_recipes(minimal, 3)
    _sync(monkeypatch, server, recipes[:2])
    # Someone renames a food in Mealie between syncs.
    renamed = server.organizers["foods"][0]
    renamed_id = renamed["id"]
    server.organizers["foods"][0] = {"id": str(uuid4()), "name": "Food 0"}
    _sync(monkeypatch, server, recipes)

    assert server.requests[("GET", "foods")] == 2
    last = server.imported[-1]
    first_food = last["recipe_ingredient"][0]["food"]
    assert first_food["name"] == "Food 0"
    assert first_food["id"] != renamed_id
    assert first_food["id"] == server.organizers["foods"][0]["id"]


def test_login_and_close_clear_cached_organizers():
    server = FakeMealie({"units": [{"id": str(uuid4()), "name": "g"}]})
    with httpx.Client(transport=httpx.MockTransport(server.handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        lookup = {RecipeUnit(name="g")}
        client._create_item_name_to_item_lookup("units", RecipeUnit, lookup)
        client._create_item_name_to_item_lookup("units", RecipeUnit, lookup)
        assert server.requests[("GET", "units")] == 1
        client.login_with_token("other-account")
        client._create_item_name_to_item_lookup("units", RecipeUnit, lookup)
        assert server.requests[("GET", "units")] == 2
        client.close()
        assert client._organizer_index == {}


def test_incomplete_inventory_is_never_cached(monkeypatch):
    calls = []

    def flaky(endpoint_name):
        calls.append(endpoint_name)
        if len(calls) == 1:
            raise mealie.MealieInventoryError("Incomplete Mealie inventory: synthetic")
        return []

    client = MealieApiClient("https://mealie.example/api")
    monkeypatch.setattr(client, "_get_all_items", flaky)
    monkeypatch.setattr(
        client,
        "_create_item",
        lambda endpoint, item: {"id": str(uuid4()), "name": item.name},
    )
    with pytest.raises(mealie.MealieInventoryError):
        client._create_item_name_to_item_lookup(
            "units", RecipeUnit, {RecipeUnit(name="g")}
        )
    client._create_item_name_to_item_lookup("units", RecipeUnit, {RecipeUnit(name="g")})
    assert calls == ["units", "units"]


def test_create_response_for_other_name_stops_before_import():
    server = FakeMealie()
    original = server.handle

    def wrong_name(request):
        if request.method == "POST" and request.url.path == "/api/foods":
            server.writes.append("foods")
            return httpx.Response(201, json={"id": str(uuid4()), "name": "Sugar"})
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(wrong_name)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        with pytest.raises(ValueError, match="different name"):
            client.create_recipe(_recipe(foods=["Salt"]))
        assert "foods" not in client._organizer_index
    assert "archive" not in server.writes


def test_failed_conflict_refresh_still_replaces_cached_inventory():
    dessert_id = str(uuid4())
    server = FakeMealie({"organizers/tags": [{"id": dessert_id, "name": "Dessert"}]})
    original = server.handle

    def conflict(request):
        if request.method == "POST" and request.url.path == "/api/organizers/tags":
            server.writes.append("organizers/tags")
            # Someone else added a second Dessert meanwhile; "vegan" stays absent.
            server.organizers["organizers/tags"].append(
                {"id": str(uuid4()), "name": "DESSERT"}
            )
            return httpx.Response(
                409, json={"detail": {"message": "This item already exists."}}
            )
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(conflict)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        with pytest.raises(httpx.HTTPStatusError):
            client.create_recipe(_recipe(tags=["vegan"]))
        # The next recipe must see the ambiguity the refresh revealed.
        with pytest.raises(MealieOrganizerAmbiguityError, match="dessert"):
            client.create_recipe(_recipe(tags=["dessert"]))
    assert "archive" not in server.writes
