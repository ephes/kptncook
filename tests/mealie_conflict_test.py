"""Only explicit organizer uniqueness conflicts permit one requery."""

from uuid import uuid4

import httpx
import pytest

from kptncook.mealie import (
    MealieApiClient,
    RecipeWithImage,
    RecipeTag,
    RecipeFood,
    RecipeUnit,
    RecipeIngredient,
)


@pytest.mark.parametrize(
    "endpoint,model",
    [("organizers/tags", RecipeTag), ("foods", RecipeFood), ("units", RecipeUnit)],
)
@pytest.mark.parametrize(
    "outcome", ["unique", "ambiguous", "absent", "unrelated", "timeout"]
)
def test_organizer_conflict_is_reconciled_once(endpoint, model, outcome):
    get_count = 0
    writes = []
    existing_id = str(uuid4())

    def handle(request):
        nonlocal get_count
        if request.method == "GET":
            get_count += 1
            rows = []
            if get_count > 1 and outcome != "absent":
                rows = [{"name": "Dessert", "id": existing_id}]
                if outcome == "ambiguous":
                    rows.append(
                        {
                            "name": "DESSERT"
                            if endpoint == "organizers/tags"
                            else "Dessert",
                            "id": str(uuid4()),
                        }
                    )
            return httpx.Response(200, json={"items": rows, "total_pages": 1})
        writes.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(
            409,
            json={
                "detail": {
                    "message": "Something else"
                    if outcome == "unrelated"
                    else "This item already exists."
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MealieApiClient("https://mealie.example/api", client=http)
        kwargs = (
            {"tags": [RecipeTag(name="dessert")]}
            if endpoint == "organizers/tags"
            else {
                "recipe_ingredient": [
                    RecipeIngredient(
                        unit=RecipeUnit(name="Dessert")
                        if endpoint == "units"
                        else None,
                        food=RecipeFood(name="Dessert")
                        if endpoint == "foods"
                        else None,
                    )
                ]
            }
        )
        recipe = RecipeWithImage(name="Synthetic", **kwargs)

        def update():
            return (
                client._update_tag_ids(recipe)
                if endpoint == "organizers/tags"
                else client._update_item_ids(
                    recipe, endpoint, model, "food" if endpoint == "foods" else "unit"
                )
            )

        if outcome == "unique":
            result = update()
            item = (
                result.tags[0]
                if endpoint == "organizers/tags"
                else getattr(
                    result.recipe_ingredient[0],
                    "food" if endpoint == "foods" else "unit",
                )
            )
            assert str(item.id) == existing_id
        else:
            with pytest.raises((httpx.HTTPStatusError, httpx.ReadTimeout)):
                update()
    assert len(writes) == 1
    assert get_count == (1 if outcome in ("unrelated", "timeout") else 2)
