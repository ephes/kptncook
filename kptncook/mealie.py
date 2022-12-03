import datetime
import io
import json
from getpass import getpass
from pathlib import Path
from typing import Any

import httpx
from pydantic import UUID4, BaseModel, Field, parse_obj_as

from .config import settings
from .models import Image
from .models import Recipe as KptnCookRecipe


class NameIsIdModel(BaseModel):
    name: str

    def __hash__(self):
        """Hash on name to be able to have sets of models."""
        return hash(self.name)

    def __eq__(self, other):
        """Compare on name to be able to subtract sets of models."""
        return self.name == other.name

    class Config:
        frozen = True  # make it hashable


class RecipeTag(NameIsIdModel):
    slug: str | None
    name: str
    group_id: UUID4 | None = Field(None, alias="groupId")
    id: UUID4 | None


class RecipeCategory(RecipeTag):
    pass


class RecipeTool(RecipeTag):
    on_hand: bool = False


class Unit(NameIsIdModel):
    id: UUID4 | None
    name: str
    description: str = ""
    fraction: bool = True
    abbreviation: str = ""


class RecipeIngredient(BaseModel):
    title: str | None
    note: str | None
    unit: Unit | None
    food: Unit | None
    disable_amount: bool = True
    quantity: float | None = 1


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
    def __init__(self, base_url):
        self.base_url = base_url
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
            all_foods_parsed[food["name"]] = Unit.parse_obj(food)

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
            all_units_parsed[unit["name"]] = Unit.parse_obj(unit)

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
        return Unit.parse_obj(r.json())

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
        return Unit.parse_obj(r.json())

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
        # download image
        r = httpx.get(image.url)
        r.raise_for_status()

        download = io.BytesIO(r.content)

        # upload to mealie
        name = Path(image.name)
        extension = name.suffix.lstrip(".")
        stem = name.stem
        data = {
            "name": stem,
            "icon": "mdi-file-image",
            "extension": extension,
        }
        r = self.post(
            f"/recipes/{recipe_slug}/assets", data=data, files={"file": download}
        )
        r.raise_for_status()

        return r.json()["fileName"]

    @staticmethod
    def _build_recipestep_text(recipe_uuid, text, image_name):
        return f'{text} <img src="/api/media/recipes/{recipe_uuid}/assets/{image_name}" height="100%" width="100%"/>'

    def enrich_recipe_with_step_images(self, recipe):
        for instruction in recipe.recipe_instructions:
            uploaded_image_name = self.upload_asset(recipe.slug, instruction.image)
            instruction.text = self._build_recipestep_text(
                recipe.id, instruction.text, uploaded_image_name
            )
        return recipe

    def _post_recipe_trunk_and_get_slug(self, recipe_name):
        data = {"name": recipe_name}
        r = self.post("/recipes", data=json.dumps(data))
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

    def _get_page(self, endpoint_name, page_num, per_page=50):
        r = self.get(f"/{endpoint_name}?page={page_num}&perPage={per_page}")
        r.raise_for_status()
        return r.json()

    def _get_all_items(self, endpoint_name):
        all_items = []
        response_data = self._get_page(endpoint_name, 1)
        all_items.extend(response_data["items"])

        # 1 was already fetched, start page_num at 2 and add 1 to the
        # number of total pages, because we start counting at 1 instead of 0
        for page_num in range(2, response_data["total_pages"] + 1):
            response_data = self._get_page(endpoint_name, page_num)
            all_items.extend(response_data["items"])

        return all_items

    def _create_item(self, endpoint_name, item):
        r = self.post(f"/{endpoint_name}", data=item.json())
        r.raise_for_status()
        return r.json()

    def _create_item_name_to_item_lookup(self, endpoint_name, model_class, items):
        existing_items = parse_obj_as(
            set[model_class], self._get_all_items(endpoint_name)
        )
        items_to_create = items - existing_items
        for item in items_to_create:
            existing_items.add(model_class(**self._create_item(endpoint_name, item)))
        return {i.name: i for i in existing_items}

    def _update_unit_ids(self, recipe):
        recipe_units = {
            ig.unit for ig in recipe.recipe_ingredient if ig.unit is not None
        }
        if len(recipe_units) == 0:
            # return early if there's nothing to do
            return recipe

        name_to_unit_with_id = self._create_item_name_to_item_lookup(
            "units", Unit, recipe_units
        )
        for ingredient in recipe.recipe_ingredient:
            if ingredient.unit is not None:
                ingredient.unit = name_to_unit_with_id[ingredient.unit.name]
        return recipe

    def _update_tag_ids(self, recipe):
        recipe_tags = {tag for tag in recipe.tags}
        if len(recipe_tags) == 0:
            # return early if there's nothing to do
            return recipe

        name_to_tag_with_id = self._create_item_name_to_item_lookup(
            "organizers/tags", RecipeTag, recipe_tags
        )
        recipe.tags = [name_to_tag_with_id[tag.name] for tag in recipe_tags]
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
        recipe = self._update_unit_ids(recipe)
        recipe = self._update_tag_ids(recipe)
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


def kptncook_to_mealie_ingredients(kptncook_ingredients):
    mealie_ingredients = []
    for ingredient in kptncook_ingredients:
        title = ingredient.ingredient.localized_title.de
        note = None
        if "," in title:
            title, note, *parts = (p.strip() for p in title.split(","))
        quantity = ingredient.quantity
        if quantity is not None:
            quantity /= 2
        measure = None
        if hasattr(ingredient, "measure"):
            if ingredient.measure is not None:
                measure = {"name": ingredient.measure}
        mealie_ingredient = RecipeIngredient(
            title=title, quantity=quantity, unit=measure, note=note
        )
        mealie_ingredients.append(mealie_ingredient)
    return mealie_ingredients


def kptncook_to_mealie_steps(steps, api_key):
    mealie_instructions = []
    for step in steps:
        image = step.image.get_image_with_api_key_url(api_key)
        mealie_instructions.append(
            RecipeStep(title=None, text=step.title.de, image=image)
        )
    return mealie_instructions


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
        "recipe_instructions": kptncook_to_mealie_steps(kcin.steps, api_key),
        "recipe_ingredient": kptncook_to_mealie_ingredients(kcin.ingredients),
        "image_url": kcin.get_image_url(api_key),
        "tags": [RecipeTag(name="kptncook")],
        "extras": {"kptncook_id": kcin.id.oid, "source": "kptncook"},
    }
    return RecipeWithImage(**kwargs)
