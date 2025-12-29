from kptncook.markdown_exporter import MarkdownExporter
from kptncook.models import Recipe


def test_replace_timers_in_step():
    exporter = MarkdownExporter()
    recipe = Recipe.model_validate(
        {
            "_id": {"$oid": "1"},
            "localizedTitle": "Timer Test",
            "authorComment": "",
            "preparationTime": 5,
            "cookingTime": 10,
            "recipeNutrition": {"calories": 100, "protein": 0, "fat": 0, "carbohydrate": 0},
            "activeTags": [],
            "steps": [
                {
                    "title": "Cook for <timer> then again for <timer>",
                    "image": {"id": "img", "name": "img.jpg", "url": "http://img", "type": "step"},
                    "timers": [{"minOrExact": 5}, {"minOrExact": 10}],
                }
            ],
            "imageList": [],
            "ingredients": [],
        }
    )

    md = exporter.render_recipe(recipe)
    assert "Cook for 5m then again for 10m" in md
