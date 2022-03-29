import datetime
import json
from getpass import getpass
from typing import Any

import httpx
from pydantic import UUID4, BaseModel, Field

from .config import settings
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

    user_id: UUID4 | None = Field(None, alias="userId")
    group_id: UUID4 | None = Field(None, alias="groupId")

    name: str | None
    slug: str = ""
    image: Any | None
    recipe_yield: str | None

    total_time: str | None = None
    prep_time: str | None = None
    cook_time: str | None = None
    perform_time: str | None = None

    description: str | None = ""
    recipe_category: list[str] | None = []
    # tags: list[RecipeTag] | None = []
    tags: list[RecipeTag | str] | None = []
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


class RecipeWithImage(Recipe):
    image_url: str


def kptncook_to_mealie(
    kcin: KptnCookRecipe, api_key: str = settings.api_key
) -> RecipeWithImage:
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
        "image_url": kcin.get_image_url(api_key),
        "tags": ["kptncook"],  # tags do not work atm
        "extras": {"kptncook_id": kcin.id.oid, "source": "kptncook"},
    }
    return RecipeWithImage(**kwargs)


class MealieApiClient:
    def __init__(self, base_url):
        self.base_url = base_url
        self.headers = {}

    @property
    def logged_in(self):
        return "access_token" in self.headers

    def to_url(self, path):
        return f"{self.base_url}{path}"

    def __getattr__(self, name):
        """
        Return proxy for httpx, joining base_url with path and
        providing authentication headers automatically.
        """

        def proxy(path, **kwargs):
            url = self.to_url(path)
            set_headers = kwargs.get("headers", {})
            kwargs["headers"] = set_headers | self.headers
            return getattr(httpx, name)(url, **kwargs)

        return proxy

    def fetch_api_token(self, username, password):
        login_data = {"username": username, "password": password}
        r = self.post("/auth/token", data=login_data)
        r.raise_for_status()
        return r.json()["access_token"]

    def login(self, username: str = "admin", password: str = ""):
        if password == "":
            password = getpass()
        access_token = self.fetch_api_token(username, password)
        self.headers = {"authorization": f"Bearer {access_token}"}

    def _post_recipe_and_get_slug(self, recipe):
        r = self.post("/recipes", data=recipe.json())
        r.raise_for_status()
        slug = r.json()
        return slug

    def _scrape_image_for_recipe(self, recipe, slug):
        json_image_url = json.dumps({"url": recipe.image_url})
        scrape_image_path = f"/recipes/{slug}/image"
        r = self.post(scrape_image_path, data=json_image_url)
        r.raise_for_status()

    def _update_user_and_group_id(self, recipe, slug):
        recipe_detail_path = f"/recipes/{slug}"
        r = self.get(recipe_detail_path)
        r.raise_for_status()
        recipe_details = r.json()
        update_attributes = ["id", "userId", "groupId"]
        updated_details = {k: recipe_details[k] for k in update_attributes}
        recipe = RecipeWithImage(**(recipe.dict() | updated_details))
        return recipe

    def _update_recipe(self, recipe, slug):
        recipe_detail_path = f"/recipes/{slug}"
        r = self.put(recipe_detail_path, data=recipe.json())
        r.raise_for_status()
        return Recipe.parse_obj(r.json())

    def create_recipe(self, recipe):
        slug = self._post_recipe_and_get_slug(recipe)
        self._scrape_image_for_recipe(recipe, slug)
        recipe = self._update_user_and_group_id(recipe, slug)
        return self._update_recipe(recipe, slug)

    def get_all_recipes(self):
        r = self.get("/recipes")
        r.raise_for_status()
        return [Recipe.parse_obj(recipe) for recipe in r.json()]

    def delete_via_slug(self, slug):
        r = self.delete(f"/recipes/{slug}")
        r.raise_for_status()
        return r.json()

    def get_via_slug(self, slug):
        r = self.get(f"/recipes/{slug}")
        r.raise_for_status()
        return Recipe.parse_obj(r.json())
