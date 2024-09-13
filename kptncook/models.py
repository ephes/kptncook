"""
All the domain models for kptncook live here.
"""

from pydantic import BaseModel, Field, model_validator


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
    typ: str
    localized_title: LocalizedString
    number_title: LocalizedString
    uncountable_title: LocalizedString | None = None
    category: str

    @model_validator(mode="before")
    def fix_json_errors(cls, values):
        if "uncountableTitle" not in values or values["uncountableTitle"] is None:
            if "numberTitle" in values:
                values["uncountableTitle"] = values["numberTitle"]
        return values

    class Config:
        alias_generator = to_camel


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
    id: RecipeId = Field(..., alias="_id")
    localized_title: LocalizedString
    author_comment: LocalizedString
    preparation_time: int
    cooking_time: int | None = None
    recipe_nutrition: Nutrition
    steps: list[RecipeStep] = Field(..., alias="steps")
    image_list: list[Image]
    ingredients: list[Ingredient]

    class Config:
        alias_generator = to_camel

    def get_image_url(self, api_key: str) -> str | None:
        try:
            [image] = [i for i in self.image_list if i.type == "cover"]
        except ValueError:
            return None
        image_url = f"{image.url}?kptnkey={api_key}"
        return image_url
