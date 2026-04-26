import datetime
import io
import json
import logging
import uuid
from collections.abc import Callable
from getpass import getpass
from pathlib import Path
from typing import Any

import httpx
from pydantic import UUID4, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .exporter_utils import get_step_text
from .config import get_settings
from .http_client import BaseHttpClient, DEFAULT_REQUEST_TIMEOUT
from .ingredient_groups import iter_ingredient_groups
from .models import (
    Image,
    Ingredient,
    RecipeStep as KptnCookRecipeStep,
    localized_fallback,
)
from .models import Recipe as KptnCookRecipe

logger = logging.getLogger(__name__)

ASSET_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class NameIsIdModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str

    def __hash__(self):
        """Hash on name to be able to have sets of models."""
        return hash(self.name)

    def __eq__(self, other):
        """Compare on name to be able to subtract sets of models."""
        return self.name == other.name


class RecipeTag(NameIsIdModel):
    slug: str | None = None
    name: str
    group_id: UUID4 | None = Field(None, alias="groupId")
    id: UUID4 | None = None


class RecipeCategory(RecipeTag):
    pass


class RecipeTool(RecipeTag):
    on_hand: bool = False


class UnitFoodBase(NameIsIdModel):
    id: UUID4 | None = None
    name: str
    description: str = ""


class RecipeFood(UnitFoodBase): ...  # noqa: E701


class RecipeUnit(UnitFoodBase):
    id: UUID4 | None = None
    fraction: bool = True
    abbreviation: str = ""


class RecipeIngredient(BaseModel):
    referenceId: UUID4 | None = None
    title: str | None = None
    note: str | None = None
    unit: RecipeUnit | None
    food: RecipeFood | None
    disable_amount: bool = True
    quantity: float | None = 1


class RecipeSummary(BaseModel):
    id: UUID4 | None = None

    user_id: UUID4 | None = Field(None, alias="userId")
    group_id: UUID4 | None = Field(None, alias="groupId")

    name: str | None = None
    slug: str = ""
    image: Any | None = None
    recipe_yield: str | None = None

    total_time: str | None = None
    prep_time: str | int | None = None
    cook_time: str | int | None = None
    perform_time: str | None = None

    description: str | None = ""
    recipe_category: list[str] | None = []
    tags: list[RecipeTag] | None = []
    # tags: list[RecipeTag | str] | None = []
    tools: list[RecipeTool] = []
    rating: int | None = None
    org_url: str | None = Field(None, alias="orgURL")

    recipe_ingredient: list[RecipeIngredient] | None = []

    date_added: datetime.date | None = None
    date_updated: datetime.datetime | None = None


class IngredientReference(BaseModel):
    referenceId: UUID4 | None = None


class RecipeStep(BaseModel):
    title: str | None = ""
    text: str
    ingredientReferences: list[IngredientReference] = []
    image: Image | None = None


class Nutrition(BaseModel):
    calories: str | None = None
    fatContent: str | None = None
    proteinContent: str | None = None
    carbohydrateContent: str | None = None
    fiberContent: str | None = None
    sodiumContent: str | None = None
    sugarContent: str | None = None


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
    file_name: str | None = None


class RecipeNote(BaseModel):
    title: str
    text: str | None = None


class Recipe(RecipeSummary):
    recipe_ingredient: list[RecipeIngredient] = []
    recipe_instructions: list[RecipeStep] | None = []
    nutrition: Nutrition | None = None

    # Mealie Specific
    settings: RecipeSettings | None = RecipeSettings()
    assets: list[RecipeAsset] | None = []
    notes: list[RecipeNote] | None = []
    extras: dict = {}


class RecipeWithImage(Recipe):
    image_url: str | None = None


class MealieApiClient(BaseHttpClient):
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            str(base_url), headers={}, timeout=DEFAULT_REQUEST_TIMEOUT, client=client
        )

    @property
    def logged_in(self):
        return "authorization" in self.headers

    def fetch_api_token(self, username, password):
        login_data = {"username": username, "password": password}
        r = self.post("/auth/token", data=login_data, timeout=60)
        r.raise_for_status()
        return r.json()["access_token"]

    def login(self, username: str = "admin", password: str = ""):
        if password == "":
            password = getpass()
        access_token = self.fetch_api_token(username, password)
        self.headers["authorization"] = f"Bearer {access_token}"

    def login_with_token(self, token: str):
        self.headers["authorization"] = f"Bearer {token}"

    def upload_asset(self, recipe_slug, image: Image):
        # download image
        r = httpx.get(
            image.url,
            follow_redirects=True,
            timeout=ASSET_DOWNLOAD_TIMEOUT,
        )
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

        return r.json()

    @staticmethod
    def _build_recipestep_text(recipe_uuid, text, image_name):
        return f'{text} <img src="/api/media/recipes/{recipe_uuid}/assets/{image_name}" height="100%" width="100%"/>'

    def enrich_recipe_with_step_images(self, recipe):
        assets = []
        for instruction in recipe.recipe_instructions:
            try:
                asset_properties = self.upload_asset(recipe.slug, instruction.image)
            except httpx.HTTPError as exc:
                logger.warning(
                    "Skipping step image upload for recipe %s: %s", recipe.slug, exc
                )
                continue
            except Exception:
                logger.exception(
                    "Skipping step image upload for recipe %s due to unexpected error",
                    recipe.slug,
                )
                continue
            uploaded_image_name = asset_properties["fileName"]
            instruction.text = self._build_recipestep_text(
                recipe.id, instruction.text, uploaded_image_name
            )
            assets.append(
                RecipeAsset(
                    name=asset_properties["name"],
                    icon=asset_properties["icon"],
                    file_name=asset_properties["fileName"],
                )
            )
        recipe.assets = assets
        return recipe

    def _post_recipe_trunk_and_get_slug(self, recipe_name):
        data = {"name": recipe_name}
        r = self.post("/recipes", json=data)
        r.raise_for_status()
        slug = r.json()
        return slug

    def _scrape_image_for_recipe(self, recipe, slug):
        if not recipe.image_url:
            return
        json_image_url = json.dumps({"url": recipe.image_url})
        scrape_image_path = f"/recipes/{slug}/image"
        r = self.post(
            scrape_image_path,
            content=json_image_url,
            headers={"Content-Type": "application/json"},
        )
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
        r = self.post(
            f"/{endpoint_name}",
            content=item.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        return r.json()

    def _create_item_name_to_item_lookup(
        self,
        endpoint_name,
        model_class,
        items,
        normalize_name: Callable[[str], str] | None = None,
    ):
        def identity(name: str) -> str:
            return name

        if normalize_name is None:
            normalize_name = identity

        existing_items = TypeAdapter(list[model_class]).validate_python(
            self._get_all_items(endpoint_name)
        )
        normalized_name_to_item = {
            normalize_name(item.name): item for item in existing_items
        }
        items_to_create = {
            item
            for item in items
            if normalize_name(item.name) not in normalized_name_to_item
        }
        for item in items_to_create:
            created_item = model_class(**self._create_item(endpoint_name, item))
            normalized_name_to_item[normalize_name(created_item.name)] = created_item
        return {
            item.name: normalized_name_to_item[normalize_name(item.name)]
            for item in items
        }

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
            "organizers/tags", RecipeTag, recipe_tags, normalize_name=str.casefold
        )
        recipe.tags = [name_to_tag_with_id[tag.name] for tag in recipe_tags]
        return recipe

    def _update_recipe(self, recipe, slug):
        recipe_detail_path = f"/recipes/{slug}"
        r = self.put(
            recipe_detail_path,
            content=recipe.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        return Recipe.model_validate(r.json())

    def create_recipe(self, recipe):
        slug = self._post_recipe_trunk_and_get_slug(recipe.name)
        logger.debug("Created Mealie recipe slug: %s", slug)
        recipe.slug = slug
        self._scrape_image_for_recipe(recipe, slug)
        recipe = self._update_user_and_group_id(recipe, slug)
        recipe = self._update_item_ids(recipe, "units", RecipeUnit, "unit")
        recipe = self._update_item_ids(recipe, "foods", RecipeFood, "food")
        recipe = self._update_tag_ids(recipe)
        recipe = self.enrich_recipe_with_step_images(recipe)
        return self._update_recipe(recipe, slug)

    @staticmethod
    def validate_recipes(recipes):
        validated_recipes = []
        for recipe in recipes:
            try:
                validated_recipe = Recipe.model_validate(recipe)
                validated_recipes.append(validated_recipe)
            except ValidationError as e:
                logger.error(
                    "Could not parse recipe {recipe_id}".format(recipe_id=recipe["id"])
                )
                logger.exception(e)
                continue
        return validated_recipes

    def get_all_recipes(self):
        all_recipes = []

        r = self.get("/recipes?page=1&perPage=50")
        r.raise_for_status()
        all_recipes.extend(self.validate_recipes(r.json()["items"]))

        page = 2
        while page <= r.json()["total_pages"]:
            r = self.get(f"/recipes?page={page}&perPage=50")
            r.raise_for_status()
            all_recipes.extend(self.validate_recipes(r.json()["items"]))
            page += 1

        return all_recipes

    def delete_via_slug(self, slug):
        r = self.delete(f"/recipes/{slug}")
        r.raise_for_status()
        return r.json()

    def get_via_slug(self, slug):
        r = self.get(f"/recipes/{slug}")
        r.raise_for_status()
        return Recipe.model_validate(r.json())


def kptncook_to_mealie_ingredients(
    kptncook_ingredients: list[Ingredient] | None,
) -> tuple[list[RecipeIngredient], dict[str, list[UUID4]]]:
    mealie_ingredients = []
    ingredient_id_to_reference_ids: dict[str, list[UUID4]] = {}
    groups = iter_ingredient_groups(kptncook_ingredients or [])
    for group_label, ingredients in groups:
        for idx, ingredient in enumerate(ingredients):
            ingredient_title = (
                localized_fallback(ingredient.ingredient.localized_title) or ""
            )
            note = None
            if "," in ingredient_title:
                ingredient_title, note, *_ = (
                    p.strip() for p in ingredient_title.split(",")
                )
            food = RecipeFood(name=ingredient_title)
            quantity = ingredient.quantity
            measure = None
            if hasattr(ingredient, "measure"):
                if ingredient.measure is not None:
                    measure = RecipeUnit(name=ingredient.measure)
            reference_id = uuid.uuid4()
            mealie_ingredient = RecipeIngredient(
                title=group_label if idx == 0 else None,
                quantity=quantity,
                unit=measure,
                note=note,
                food=food,
                referenceId=reference_id,
            )
            mealie_ingredients.append(mealie_ingredient)
            ingredient_id = None
            if ingredient.ingredient.id is not None:
                ingredient_id = ingredient.ingredient.id.oid
            if ingredient_id:
                ingredient_id_to_reference_ids.setdefault(ingredient_id, []).append(
                    reference_id
                )
    return mealie_ingredients, ingredient_id_to_reference_ids


def kptncook_to_mealie_steps(
    steps: list[KptnCookRecipeStep],
    api_key: str,
    ingredient_id_to_reference_ids: dict[str, list[UUID4]],
):
    mealie_instructions = []
    for step in steps:
        image = step.image.get_image_with_api_key_url(api_key)
        reference_ids: list[UUID4] = []
        if step.ingredients:
            for ingredient in step.ingredients:
                if ingredient is None:
                    continue
                ingredient_id = ingredient.ingredient_id
                if (
                    ingredient_id is None
                    and ingredient.ingredient is not None
                    and ingredient.ingredient.id is not None
                ):
                    ingredient_id = ingredient.ingredient.id.oid
                if ingredient_id is None:
                    continue
                reference_ids.extend(
                    ingredient_id_to_reference_ids.get(ingredient_id, [])
                )
        seen = set()
        ingredient_references = []
        for reference_id in reference_ids:
            if reference_id in seen:
                continue
            seen.add(reference_id)
            ingredient_references.append(IngredientReference(referenceId=reference_id))
        mealie_instructions.append(
            RecipeStep(
                title=None,
                text=get_step_text(step),
                image=image,
                ingredientReferences=ingredient_references,
            )
        )
    return mealie_instructions


def kptncook_to_mealie_tags(active_tags: list[str] | None) -> list[RecipeTag]:
    tag_names = ["kptncook"]
    if active_tags:
        tag_names.extend(active_tags)

    seen = set()
    tags = []
    for name in tag_names:
        if not name or name in seen:
            continue
        seen.add(name)
        tags.append(RecipeTag.model_validate({"name": name, "group_id": None}))
    return tags


def kptncook_to_mealie(
    kcin: KptnCookRecipe, api_key: str | None = None
) -> RecipeWithImage:
    if api_key is None:
        api_key = get_settings().kptncook_api_key
    ingredients, ingredient_id_to_reference_ids = kptncook_to_mealie_ingredients(
        kcin.ingredients
    )
    author_comment = localized_fallback(kcin.author_comment)
    kwargs = {
        "name": localized_fallback(kcin.localized_title),
        "notes": (
            [RecipeNote(title="author comment", text=author_comment)]
            if author_comment
            else []
        ),
        "nutrition": Nutrition(
            calories=str(kcin.recipe_nutrition.calories),
            proteinContent=str(kcin.recipe_nutrition.protein),
            fatContent=str(kcin.recipe_nutrition.fat),
            carbohydrateContent=str(kcin.recipe_nutrition.carbohydrate),
        ),
        "prep_time": kcin.preparation_time,
        "cook_time": kcin.cooking_time,
        "recipe_yield": "1 Portionen",
        "recipe_instructions": kptncook_to_mealie_steps(
            kcin.steps, api_key, ingredient_id_to_reference_ids
        ),
        "recipe_ingredient": ingredients,
        "image_url": kcin.get_image_url(api_key),
        "tags": kptncook_to_mealie_tags(kcin.active_tags),
        "extras": {"kptncook_id": kcin.id.oid, "source": "kptncook"},
    }
    logger.debug("Mealie recipe payload kwargs: %s", kwargs)
    return RecipeWithImage(**kwargs)
