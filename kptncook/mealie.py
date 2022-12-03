import datetime
import json

# import os
import tempfile
from getpass import getpass
from typing import Any

import httpx
from pydantic import UUID4, BaseModel, Field

from .config import settings
from .models import Image
from .models import Recipe as KptnCookRecipe


class RecipeTag(BaseModel):
    slug: str | None
    name: str
    group_id: UUID4 | None = Field(None, alias="groupId")
    id: UUID4 | None


class RecipeCategory(RecipeTag):
    pass


class RecipeTool(RecipeTag):
    on_hand: bool = False


class UnitFoodBase(BaseModel):
    id: UUID4 | None
    name: str
    description: str = ""


class RecipeIngredient(BaseModel):
    title: str | None
    note: str | None
    unit: UnitFoodBase | None
    food: UnitFoodBase | None
    disableAmount: bool = True
    quantity: float | None


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
    tags: list[RecipeTag] | None = []
    # tags: list[RecipeTag | str] | None = []
    tools: list[RecipeTool] = []
    rating: int | None
    org_url: str | None = Field(None, alias="orgURL")

    recipe_ingredient: list[RecipeIngredient] | None = []

    date_added: datetime.date | None
    date_updated: datetime.datetime | None


class RecipeStep(BaseModel):
    title: str | None = ""
    text: str
    ingredientReferences: list[Any] = []
    image: Image | None


class Nutrition(BaseModel):
    calories: str | None
    fat_content: str | None
    protein_content: str | None
    carbohydrate_content: str | None
    fiber_content: str | None
    sodium_content: str | None
    sugar_content: str | None


class RecipeSettings(BaseModel):
    public: bool = True
    show_nutrition: bool = True
    show_assets: bool = False
    landscape_view: bool = False
    disable_comments: bool = False
    disable_amount: bool = False
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
    extras: dict = {}


class RecipeWithImage(Recipe):
    image_url: str


class MealieApiClient:
    def __init__(self, base_url, kptncook_api_key):
        self.base_url = base_url
        self.kptncook_api_key = kptncook_api_key
        self.headers = {}
        self.foods_cache = {}
        self.units_cache = {}
        self.tags_cache = {}

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
        r = self.post("/auth/token", data=login_data, timeout=60)
        r.raise_for_status()
        return r.json()["access_token"]

    def login(self, username: str = "admin", password: str = ""):
        if password == "":
            password = getpass()
        access_token = self.fetch_api_token(username, password)
        self.headers = {"authorization": f"Bearer {access_token}"}

    def get_all_tags(self):
        all_tags = []

        r = self.get("/organizers/tags?page=1&perPage=50")
        r.close()
        r.raise_for_status()
        all_tags.extend(r.json()["items"])

        page = 2
        while page <= r.json()["total_pages"]:
            r = self.get(f"/organizers/tags?page={page}&perPage=50")
            r.raise_for_status()
            all_tags.extend(r.json()["items"])
            page += 1

        # all_tags_parsed = []
        all_tags_parsed = {}
        for tag in all_tags:
            # all_tags_parsed.extend(RecipeTag.parse_obj(tag))
            all_tags_parsed[tag["name"]] = RecipeTag.parse_obj(tag)

        return all_tags_parsed

    def get_all_foods(self):
        all_foods = []

        r = self.get("/foods?page=1&perPage=50")
        r.raise_for_status()
        all_foods.extend(r.json()["items"])

        page = 2
        while page <= r.json()["total_pages"]:
            r = self.get(f"/foods?page={page}&perPage=50")
            r.raise_for_status()
            all_foods.extend(r.json()["items"])
            page += 1

        # all_foods_parsed = []
        all_foods_parsed = {}
        for food in all_foods:
            # all_foods_parsed.extend(UnitFoodBase.parse_obj(food))
            all_foods_parsed[food["name"]] = UnitFoodBase.parse_obj(food)

        return all_foods_parsed

    def get_all_units(self):
        all_units = []

        r = self.get("/units?page=1&perPage=50")
        r.raise_for_status()
        all_units.extend(r.json()["items"])

        page = 2
        while page <= r.json()["total_pages"]:
            r = self.get(f"/units?page={page}&perPage=50")
            r.raise_for_status()
            all_units.extend(r.json()["items"])
            page += 1

        # all_units_parsed = []
        all_units_parsed = {}
        for unit in all_units:
            # all_units_parsed.extend(UnitFoodBase.parse_obj(unit))
            all_units_parsed[unit["name"]] = UnitFoodBase.parse_obj(unit)

        return all_units_parsed

    def create_tag(self, tag):
        tag_json = {"name": tag}
        r = self.post("/organizers/tags", data=json.dumps(tag_json))
        r.raise_for_status()
        return RecipeTag.parse_obj(r.json())

    def create_food(self, food):
        food_json = {"id": "", "name": food, "description": ""}
        r = self.post("/foods", data=json.dumps(food_json))
        r.raise_for_status()
        return UnitFoodBase.parse_obj(r.json())

    def create_unit(self, unit):
        unit_json = {
            "id": "",
            "name": unit,
            "description": "",
            "fraction": True,
            "abbreviation": "",
        }
        r = self.post("/units", data=json.dumps(unit_json))
        r.raise_for_status()
        return UnitFoodBase.parse_obj(r.json())

    def get_tag_by_name(self, name):
        if len(self.tags_cache) == 0:
            self.tags_cache = self.get_all_tags()

        if name in self.tags_cache:
            return self.tags_cache[name]
        else:
            tag = self.create_tag(name)
            self.tags_cache = self.get_all_tags()
            return tag

    def get_food_by_name(self, name):
        if len(self.foods_cache) == 0:
            self.foods_cache = self.get_all_foods()

        if name in self.foods_cache:
            return self.foods_cache[name]
        else:
            food = self.create_food(name)
            self.foods_cache = self.get_all_foods()
            return food

    def get_unit_by_name(self, name):
        if name is None:
            return None

        if len(self.units_cache) == 0:
            self.units_cache = self.get_all_units()

        if name in self.units_cache:
            return self.units_cache[name]
        else:
            unit = self.create_unit(name)
            self.units_cache = self.get_all_units()
            return unit

    def upload_asset(self, recipe_slug, image: Image):
        download_file = tempfile.NamedTemporaryFile()

        # download from kptncook
        r = httpx.get(f"{image.url}?kptnkey={self.kptncook_api_key}")
        r.raise_for_status()
        download_file.write(r.content)

        # upload to mealie
        data = {
            "name": image.name.split(".")[0],
            "icon": "mdi-file-image",
            "extension": image.name.split(".")[-1],
        }
        r = self.post(
            f"/recipes/{recipe_slug}/assets", data=data, files={"file": download_file}
        )
        download_file.close()
        r.raise_for_status()

        return r.json()["fileName"]

    def _build_recipestep_text(self, recipe_uuid, text, image_name):
        return f'{text} <img src="/api/media/recipes/{recipe_uuid}/assets/{image_name}" height="100%" width="100%"/>'

    def enrich_recipe_with_step_images(self, recipe):
        for i in range(len(recipe.recipeInstructions)):
            uploaded_image_name = self.upload_asset(
                recipe.slug, recipe.recipeInstructions[i].image
            )
            recipe.recipeInstructions[i].text = self._build_recipestep_text(
                recipe.id, recipe.recipeInstructions[i].text, uploaded_image_name
            )

        return recipe

    def _post_recipe_trunk_and_get_slug(self, recipe_name):
        data = {"name": recipe_name}
        r = self.post("/recipes", data=json.dumps(data))
        r.raise_for_status()
        slug = r.json()
        return slug

    def _scrape_image_for_recipe(self, recipe, slug):
        json_image_url = json.dumps({"url": recipe.imageUrl})
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
        # for tag in recipe.tags:
        #    tag.groupId = recipe_details["groupId"]
        return recipe

    def _update_recipe(self, recipe, slug):
        recipe_detail_path = f"/recipes/{slug}"
        r = self.put(recipe_detail_path, data=recipe.json())
        print(r.text)
        r.raise_for_status()
        return Recipe.parse_obj(r.json())

    def create_recipe(self, recipe):
        slug = self._post_recipe_trunk_and_get_slug(recipe.name)
        print(slug)
        recipe.slug = slug
        self._scrape_image_for_recipe(recipe, slug)
        recipe = self._update_user_and_group_id(recipe, slug)
        recipe = self.enrich_recipe_with_step_images(recipe)
        return self._update_recipe(recipe, slug)

    def get_all_recipes(self):
        all_recipes = []

        r = self.get("/recipes?page=1&perPage=50")
        r.raise_for_status()
        all_recipes.extend([Recipe.parse_obj(recipe) for recipe in r.json()["items"]])

        page = 2
        while page <= r.json()["total_pages"]:
            r = self.get(f"/recipes?page={page}&perPage=50")
            r.raise_for_status()
            all_recipes.extend(
                [Recipe.parse_obj(recipe) for recipe in r.json()["items"]]
            )
            page += 1

        return all_recipes

    def delete_via_slug(self, slug):
        r = self.delete(f"/recipes/{slug}")
        r.raise_for_status()
        return r.json()

    def get_via_slug(self, slug):
        r = self.get(f"/recipes/{slug}")
        r.raise_for_status()
        return Recipe.parse_obj(r.json())


# def kptncook_to_mealie(kcin: KptnCookRecipe,
# mealie_client: MealieApiClient, api_key: str = settings.kptncook_api_key) -> RecipeWithImage:
def kptncook_to_mealie(
    kcin: KptnCookRecipe, api_key: str = settings.kptncook_api_key
) -> RecipeWithImage:
    kwargs = {
        "name": kcin.localized_title.de,
        "notes": [
            RecipeNote(title="author comment", text=kcin.author_comment.de),
        ],
        "nutrition": Nutrition(
            calories=kcin.recipe_nutrition.calories,
            proteinContent=kcin.recipe_nutrition.protein,
            fatContent=kcin.recipe_nutrition.fat,
            carbohydrateContent=kcin.recipe_nutrition.carbohydrate,
        ),  # type: ignore
        "prep_time": kcin.preparation_time,
        "cook_time": kcin.cooking_time,
        "recipe_yield": "1 Portionen",  # kptncook serves for default 2 portions, but we want it more granular
        "recipe_instructions": [
            RecipeStep(title=None, text=step.title.de, image=step.image)
            for step in kcin.steps
        ],
        # "recipeIngredient": [
        #     RecipeIngredient(food=mealie_client.get_food_by_name(
        #     ig.ingredient.localized_title.de.split(",")[0]), quantity=(ig.quantity/2.0)
        #     if ig.quantity is not None else None,
        #     unit=mealie_client.get_unit_by_name(ig.measure if hasattr(ig, "measure") else None),
        #     note=ig.ingredient.localized_title.de.split(",")[1]
        #     if len(ig.ingredient.localized_title.de.split(","))>1 else None)  # type: ignore
        #     for ig in kcin.ingredients
        # ],  # FIXME only to avoid passing mealie_client
        "image_url": kcin.get_image_url(api_key),
        # "tags": ["kptncook"],  # tags do not work atm
        # "tags": [RecipeTag(name="kptncook")],
        # "tags": [mealie_client.get_tag_by_name(tag) for tag in ["kptncook"]],  # FIXME avoid passing mealie_client
        "extras": {"kptncook_id": kcin.id.oid, "source": "kptncook"},
    }
    return RecipeWithImage(**kwargs)
