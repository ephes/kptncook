"""
All the domain models for kptncook live here.
"""

from pydantic import BaseModel, Field


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase."""
    words = []
    for num, word in enumerate(string.split("_")):
        if num > 0:
            word = word.capitalize()
        words.append(word)
    return "".join(words)


class LocalizedString(BaseModel):
    en: str | None
    de: str | None
    es: str | None
    fr: str | None
    pt: str | None


class Nutrition(BaseModel):
    calories: int
    protein: int
    fat: int
    carbohydrate: int


class Image(BaseModel):
    name: str
    type: str | None
    url: str


class IngredientDetails(BaseModel):
    typ: str
    localized_title: LocalizedString
    number_title: LocalizedString
    category: str

    class Config:
        alias_generator = to_camel


class Ingredient(BaseModel):
    quantity: float
    ingredient: IngredientDetails


class RecipeId(BaseModel):
    oid: str = Field(..., alias="$oid")


class Recipe(BaseModel):
    id: RecipeId = Field(..., alias="_id")
    localized_title: LocalizedString
    country: str
    author_comment: LocalizedString
    preparation_time: int
    cooking_time: int | None
    recipe_nutrition: Nutrition
    steps_en: list[str] = Field(..., alias="stepsEN")
    steps_de: list[str] = Field(..., alias="stepsDE")
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
