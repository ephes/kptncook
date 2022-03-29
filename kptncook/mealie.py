import datetime
from typing import Any

from pydantic import UUID4, BaseModel, Field

from .models import Recipe as KptnCookRecipe


class RecipeTag(BaseModel):
    name: str


class RecipeCategory(RecipeTag):
    pass


class RecipeTool(RecipeTag):
    on_hand: bool = False


class UnitFoodBase(BaseModel):
    name: str
    description: str = ""


class RecipeIngredient(BaseModel):
    title: str | None
    note: str | None
    unit: UnitFoodBase | None
    food: UnitFoodBase | None
    disable_amount: bool = True
    quantity: float = 1


class RecipeSummary(BaseModel):
    id: UUID4 | None

    user_id: UUID4 | None = None
    group_id: UUID4 | None = None

    name: str | None
    slug: str = ""
    image: Any | None
    recipe_yield: str | None

    total_time: str | None = None
    prep_time: str | None = None
    cook_time: str | None = None
    perform_time: str | None = None

    description: str | None = ""
    recipe_category: list[RecipeCategory] | None = []
    tags: list[RecipeTag] | None = []
    tools: list[RecipeTool] = []
    rating: int | None
    org_url: str | None = Field(None, alias="orgURL")

    recipe_ingredient: list[RecipeIngredient] | None = []

    date_added: datetime.date | None
    date_updated: datetime.datetime | None


class RecipeStep(BaseModel):
    title: str | None = ""
    text: str
    ingredient_references: list[Any] = []


class Nutrition(BaseModel):
    calories: str | None
    fat_content: str | None
    protein_content: str | None
    carbohydrate_content: str | None
    fiber_content: str | None
    sodium_content: str | None
    sugar_content: str | None


class RecipeSettings(BaseModel):
    public: bool = False
    show_nutrition: bool = False
    show_assets: bool = False
    landscape_view: bool = False
    disable_comments: bool = True
    disable_amount: bool = True
    locked: bool = False


class RecipeAsset(BaseModel):
    name: str
    icon: str
    file_name: str | None


class RecipeNote(BaseModel):
    title: str
    text: str | None


class Recipe(RecipeSummary):
    recipe_ingredient: list[RecipeIngredient] = []
    recipe_instructions: list[RecipeStep] | None = []
    nutrition: Nutrition | None

    # Mealie Specific
    settings: RecipeSettings | None = RecipeSettings()
    assets: list[RecipeAsset] | None = []
    notes: list[RecipeNote] | None = []
    extras: dict | None = {}


def kptncook_to_mealie(kcin: KptnCookRecipe):
    kwargs = {
        "name": kcin.localized_title.de,
        "notes": [
            RecipeNote(title="author comment", text=kcin.author_comment.de),
        ],
        "nutrition": Nutrition(
            calories=kcin.recipe_nutrition.calories,
            protein_content=kcin.recipe_nutrition.protein,
            fat_content=kcin.recipe_nutrition.fat,
            carbohydrate_content=kcin.recipe_nutrition.carbohydrate,
        ),  # type: ignore
        "prep_time": kcin.preparation_time,
        "cook_time": kcin.cooking_time,
        "recipe_instructions": [
            RecipeStep(title=None, text=step) for step in kcin.steps_de
        ],
        "recipe_ingredient": [
            RecipeIngredient(title=ig.ingredient.localized_title.de)  # type: ignore
            for ig in kcin.ingredients
        ],
    }
    return Recipe(**kwargs)
