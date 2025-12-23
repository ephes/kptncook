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

    def fallback(self) -> str | None:
        for candidate in (self.de, self.en, self.es, self.fr, self.pt):
            if candidate:
                return candidate
        return None

    @model_validator(mode="before")
    def coerce_string(cls, value: Any) -> dict[str, Any] | Any:
        if isinstance(value, str):
            # Default to German for string-only payloads.
            return {"de": value}
        if isinstance(value, dict):
            singular = value.get("singular")
            plural = value.get("plural")
            uncountable = value.get("uncountable")
            for candidate in (singular, plural, uncountable):
                if isinstance(candidate, dict):
                    # Prefer localized singular/plural if present.
                    return candidate
            for candidate in (uncountable, singular, plural):
                if isinstance(candidate, str):
                    # Fall back to the raw string if only singular/plural are present.
                    return {"de": candidate}
        return value


class LocalizedStepIngredientUnit(LocalizedString):
    """Localized unit labels for step ingredients."""


def localized_fallback(value: LocalizedString | None) -> str | None:
    if value is None:
        return None
    return value.fallback()


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
                for key in ("uncountableTitle", "title", "numberTitle", "name"):
                    if values.get(key) is not None:
                        values["localizedTitle"] = values[key]
                        break
            if values.get("numberTitle") is None:
                for key in ("localizedTitle", "uncountableTitle", "title", "name"):
                    if values.get(key) is not None:
                        values["numberTitle"] = values[key]
                        break
            if values.get("uncountableTitle") is None and "numberTitle" in values:
                values["uncountableTitle"] = values["numberTitle"]
        return values


class StepIngredientUnit(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    name: str | None = None
    localized_title: LocalizedStepIngredientUnit | None = None
    short_title: LocalizedStepIngredientUnit | None = None

    @model_validator(mode="before")
    def coerce_unit_string(cls, value: Any) -> dict[str, Any] | Any:
        if isinstance(value, str):
            return {"name": value}
        return value


class Ingredient(BaseModel):
    quantity: float | None = None
    measure: str | None = None
    ingredient: IngredientDetails


class RecipeId(BaseModel):
    oid: str = Field(..., alias="$oid")


class StepIngredientDetails(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    id: RecipeId | None = Field(None, alias="_id")
    typ: str | None = None
    localized_title: LocalizedString | None = None
    number_title: LocalizedString | None = None
    uncountable_title: LocalizedString | None = None
    category: str | None = None

    @model_validator(mode="before")
    def coerce_titles(cls, values):
        if isinstance(values, str):
            return {"localizedTitle": values}
        if isinstance(values, dict):
            if values.get("localizedTitle") is None:
                for key in ("uncountableTitle", "title", "name", "numberTitle"):
                    if values.get(key) is not None:
                        values["localizedTitle"] = values[key]
                        break
            if (
                values.get("uncountableTitle") is None
                and values.get("numberTitle") is not None
            ):
                values["uncountableTitle"] = values["numberTitle"]
        return values


class StepIngredient(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    quantity: float | None = None
    unit: StepIngredientUnit | None = None
    ingredient: StepIngredientDetails | None = None

    @model_validator(mode="before")
    def normalize_fields(cls, values):
        if isinstance(values, dict):
            if values.get("quantity") is None and values.get("amount") is not None:
                values["quantity"] = values["amount"]
            if values.get("unit") is None and values.get("measure") is not None:
                values["unit"] = values["measure"]
        return values


class RecipeStep(BaseModel):
    title: LocalizedString
    image: Image
    ingredients: list[StepIngredient | None] | None = None


class Recipe(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    id: RecipeId = Field(..., alias="_id")
    uid: str | None = None
    rtype: str | None = None
    localized_title: LocalizedString
    author_comment: LocalizedString
    preparation_time: int
    cooking_time: int | None = None
    recipe_nutrition: Nutrition
    active_tags: list[str] | None = None
    steps: list[RecipeStep] = Field(..., alias="steps")
    image_list: list[Image]
    ingredients: list[Ingredient]

    @model_validator(mode="before")
    def normalize_titles(cls, values):
        if isinstance(values, dict):
            if values.get("localizedTitle") is None:
                title = values.get("title") or values.get("name")
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
