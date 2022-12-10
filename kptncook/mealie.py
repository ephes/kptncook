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


class UnitFoodBase(NameIsIdModel):
    id: UUID4 | None
    name: str
    description: str = ""


class RecipeFood(UnitFoodBase):
    ...


class RecipeUnit(UnitFoodBase):
    fraction: bool = True
    abbreviation: str = ""


class RecipeIngredient(BaseModel):
    title: str | None
    note: str | None
    unit: RecipeUnit | None
    food: RecipeUnit | None
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
            with httpx.Client() as client:
                response = getattr(client, name)(url, **kwargs)
            return response

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

    def _update_item_ids(self, recipe, endpoint_name, model_class, attr_name):
        items = {
            getattr(ig, attr_name)
            for ig in recipe.recipe_ingredient
            if getattr(ig, attr_name) is not None
        }
        if len(items) == 0:
            # return early if there's nothing to do
            return recipe

        name_to_item_with_id = self._create_item_name_to_item_lookup(
            endpoint_name, model_class, items
        )
        for ingredient in recipe.recipe_ingredient:
            if getattr(ingredient, attr_name) is not None:
                setattr(
                    ingredient,
                    attr_name,
                    name_to_item_with_id[getattr(ingredient, attr_name).name],
                )
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
        recipe = self._update_item_ids(recipe, "units", RecipeUnit, "unit")
        recipe = self._update_item_ids(recipe, "foods", RecipeFood, "food")
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
        food = {"name": title}
        quantity = ingredient.quantity
        measure = None
        if hasattr(ingredient, "measure"):
            if ingredient.measure is not None:
                measure = {"name": ingredient.measure}
        mealie_ingredient = RecipeIngredient(
            title=title, quantity=quantity, unit=measure, note=note, food=food
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
        "recipe_yield": "1 Portionen",
        "recipe_instructions": kptncook_to_mealie_steps(kcin.steps, api_key),
        "recipe_ingredient": kptncook_to_mealie_ingredients(kcin.ingredients),
        "image_url": kcin.get_image_url(api_key),
        "tags": [RecipeTag(name="kptncook")],
        "extras": {"kptncook_id": kcin.id.oid, "source": "kptncook"},
    }
    return RecipeWithImage(**kwargs)
