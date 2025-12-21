"""
All the domain models for kptncook live here.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase."""
    words = []
    for num, word in enumerate(string.split("_")):
        if num > 0:
            word = word.capitalize()
        words.append(word)
    return "".join(words)


class LocalizedString(BaseModel):
    en: str | None = None
    de: str | None = None
    es: str | None = None
    fr: str | None = None
    pt: str | None = None

    @model_validator(mode="before")
    def coerce_string(cls, value: Any) -> dict[str, Any] | Any:
        if isinstance(value, str):
            # Default to German for string-only payloads.
            return {"de": value}
        if isinstance(value, dict):
            singular = value.get("singular")
            if isinstance(singular, dict):
                # Prefer singular if both singular/plural are present.
                return singular
            plural = value.get("plural")
            if isinstance(plural, dict):
                return plural
        return value


class Nutrition(BaseModel):
    calories: int
    protein: int
    fat: int
    carbohydrate: int


class Image(BaseModel):
    name: str
    type: str | None = None
    url: str

    def get_image_with_api_key_url(self, api_key: str) -> "Image":
        url_with_key = f"{self.url}?kptnkey={api_key}"
        kwargs = self.model_dump() | {"url": url_with_key}
        return Image(**kwargs)


class IngredientDetails(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    typ: str
    localized_title: LocalizedString
    number_title: LocalizedString
    uncountable_title: LocalizedString | None = None
    category: str

    @model_validator(mode="before")
    def fix_json_errors(cls, values):
        if isinstance(values, dict):
            if values.get("localizedTitle") is None:
                for key in ("uncountableTitle", "numberTitle", "title"):
                    if values.get(key) is not None:
                        values["localizedTitle"] = values[key]
                        break
            if values.get("uncountableTitle") is None and "numberTitle" in values:
                values["uncountableTitle"] = values["numberTitle"]
        return values


class Ingredient(BaseModel):
    quantity: float | None = None
    measure: str | None = None
    ingredient: IngredientDetails


class RecipeId(BaseModel):
    oid: str = Field(..., alias="$oid")


class RecipeStep(BaseModel):
    title: LocalizedString
    image: Image


class Recipe(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    id: RecipeId = Field(..., alias="_id")
    localized_title: LocalizedString
    author_comment: LocalizedString
    preparation_time: int
    cooking_time: int | None = None
    recipe_nutrition: Nutrition
    steps: list[RecipeStep] = Field(..., alias="steps")
    image_list: list[Image]
    ingredients: list[Ingredient]

    @model_validator(mode="before")
    def normalize_titles(cls, values):
        if isinstance(values, dict):
            if values.get("localizedTitle") is None:
                title = values.get("title")
                if title is not None:
                    values["localizedTitle"] = title
        return values

    def get_image_url(self, api_key: str) -> str | None:
        try:
            [image] = [i for i in self.image_list if i.type == "cover"]
        except ValueError:
            return None
        image_url = f"{image.url}?kptnkey={api_key}"
        return image_url
