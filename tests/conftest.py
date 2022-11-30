import json
from pathlib import Path

import pytest


@pytest.fixture
def full_recipe():
    example_path = Path(__file__).parent / "fixtures" / "kptncook_example.json"
    with example_path.open("r") as f:
        example = json.load(f)
    return example


@pytest.fixture
def minimal():
    return {
        "_id": {"$oid": "5e5390e2740000cdf1381c64"},
        "localizedTitle": {"de": "Minimal Recipe"},
        "country": "us/de/ww",
        "authorComment": {"de": "Dies ist ein Kommentar"},
        "preparationTime": 20,
        "recipeNutrition": {
            "calories": 100,
            "fat": 10,
            "carbohydrate": 20,
            "protein": 30,
        },
        "steps": [
            {
                "title": {"de": "Alles parat?"},
                "ingredients": [],
                "image": {
                    "name": "REZ_6666_11.jpg",
                    "url": "https://d2am1qai33sroc.cloudfront.net/image/63652er8d4b00007500b0c51d",
                    "type": "step",
                },
            }
        ],
        "imageList": [
            {
                "name": "REZ_1837_Cover.jpg",
                "type": "cover",
                "url": "https://d2am1qai33sroc.cloudfront.net/image/f6813160-68a5-420f-8c77-be9dcc2fa91b",
            },
        ],
        "ingredients": [],
    }
