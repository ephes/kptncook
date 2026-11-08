import datetime
import io
import json
import logging
import math
import re
import uuid
from time import monotonic
from html import escape
from urllib.parse import quote, urlsplit
from collections.abc import Callable
from getpass import getpass
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
from pydantic import UUID4, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.alias_generators import to_camel

from .exporter_utils import get_step_text
from .config import get_settings
from .http_errors import extract_mealie_detail_message
from .http_client import BaseHttpClient, DEFAULT_REQUEST_TIMEOUT
from .image_download import download_image
from .image_origin import ImageOriginError, is_allowed_image_url
from .ingredient_groups import iter_ingredient_groups
from .models import (
    Image,
    Ingredient,
    RecipeStep as KptnCookRecipeStep,
    localized_fallback,
    scale_quantity,
)
from .models import Recipe as KptnCookRecipe

logger = logging.getLogger(__name__)

MAX_INVENTORY_PAGES = 1000
COVER_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp"})
MAX_INVENTORY_ITEMS = 50_000
DEFAULT_INVENTORY_TIMEOUT_SECONDS = 120.0


def _recipe_path(slug: str) -> str:
    """Build one authenticated route from a supported, unambiguous slug."""
    if (
        not isinstance(slug, str)
        or not slug
        or not all(char.isalnum() or char in "-_" for char in slug)
    ):
        raise ValueError("Invalid Mealie recipe slug")
    return f"/recipes/{quote(slug, safe='-_')}"


def _media_failure_reason(exc: BaseException) -> str:
    """A log-safe reason for an optional media failure (never the URL)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _same_name(name: str) -> str:
    return name


ORGANIZER_LABELS = {"units": "unit", "foods": "food", "organizers/tags": "tag"}


class MealieOrganizerAmbiguityError(ValueError):
    """Several existing organizers with different IDs match one name."""


class MealieInventoryError(ValueError):
    """An incomplete inventory must not be used to decide remote writes."""


class MealieModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class NameIsIdModel(MealieModel):
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


class RecipeIngredient(MealieModel):
    referenceId: UUID4 | None = None
    title: str | None = None
    note: str | None = None
    unit: RecipeUnit | None
    food: RecipeFood | None
    disable_amount: bool = True
    quantity: float | None = 1


class RecipeSummary(MealieModel):
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
    recipe_category: list[RecipeCategory | str] | None = []
    tags: list[RecipeTag] | None = []
    # tags: list[RecipeTag | str] | None = []
    tools: list[RecipeTool] = []
    rating: float | None = None
    org_url: str | None = Field(None, alias="orgURL")

    recipe_ingredient: list[RecipeIngredient] | None = []

    date_added: datetime.date | None = None
    date_updated: datetime.datetime | None = None


class IngredientReference(MealieModel):
    referenceId: UUID4 | None = None


class RecipeStep(MealieModel):
    title: str | None = ""
    text: str
    ingredientReferences: list[IngredientReference] = []
    image: Image | None = None


class Nutrition(MealieModel):
    calories: str | None = None
    fatContent: str | None = None
    proteinContent: str | None = None
    carbohydrateContent: str | None = None
    fiberContent: str | None = None
    sodiumContent: str | None = None
    sugarContent: str | None = None


class RecipeSettings(MealieModel):
    public: bool = True
    show_nutrition: bool = True
    show_assets: bool = False
    landscape_view: bool = False
    disable_comments: bool = False
    disable_amount: bool = False
    locked: bool = False


class RecipeAsset(MealieModel):
    name: str
    icon: str
    file_name: str | None = None


class RecipeNote(MealieModel):
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
        inventory_timeout: float = DEFAULT_INVENTORY_TIMEOUT_SECONDS,
    ) -> None:
        if (
            isinstance(inventory_timeout, bool)
            or not math.isfinite(inventory_timeout)
            or not 0 < inventory_timeout <= 86_400
        ):
            raise ValueError(
                "Mealie inventory timeout must be finite, positive and at most 86400 seconds"
            )
        self.inventory_timeout = float(inventory_timeout)
        super().__init__(
            str(base_url), headers={}, timeout=DEFAULT_REQUEST_TIMEOUT, client=client
        )
        self._archive_endpoint: str | None = None
        # Organizer (units/foods/tags) lookup state for one sync on this client:
        # endpoint -> normalized name -> distinct existing items. Filled from a
        # complete inventory, then updated only by validated creates and
        # explicit 409 refreshes. Cleared on login, token change and close.
        self._organizer_index: dict[str, dict[str, list[Any]]] = {}

    @property
    def logged_in(self):
        return "authorization" in self.headers

    def fetch_api_token(self, username, password):
        login_data = {"username": username, "password": password}
        r = self.post("/auth/token", data=login_data, timeout=60)
        r.raise_for_status()
        try:
            payload = r.json()
        except ValueError:
            raise ValueError("Invalid Mealie login response") from None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Invalid Mealie login response: expected a nonempty token")
        return token

    def login(self, username: str = "admin", password: str = ""):
        if password == "":
            password = getpass()
        access_token = self.fetch_api_token(username, password)
        self.headers["authorization"] = f"Bearer {access_token}"
        self.clear_organizer_cache()

    def login_with_token(self, token: str):
        self.headers["authorization"] = f"Bearer {token}"
        self.clear_organizer_cache()

    def clear_organizer_cache(self) -> None:
        """Forget cached units/foods/tags so the next lookup rereads Mealie."""
        self._organizer_index = {}

    def close(self) -> None:
        self.clear_organizer_cache()
        super().close()

    def upload_asset(self, recipe_slug, image: Image):
        asset_path = _recipe_path(recipe_slug) + "/assets"
        # download image
        download = io.BytesIO(download_image(image.url, follow_redirects=True))

        # upload to mealie
        name = Path(image.name)
        extension = name.suffix.lstrip(".")
        stem = name.stem
        data = {
            "name": stem,
            "icon": "mdi-file-image",
            "extension": extension,
        }
        r = self.post(asset_path, data=data, files={"file": download})
        r.raise_for_status()

        return r.json()

    @staticmethod
    def _build_recipestep_text(recipe_uuid, text, image_name):
        components = [str(recipe_uuid), image_name]
        if any(component in ("", ".", "..") for component in components):
            raise ValueError("Invalid Mealie media path component")
        recipe_path, image_path = (
            quote(component, safe="") for component in components
        )
        media_url = escape(
            f"/api/media/recipes/{recipe_path}/assets/{image_path}", quote=True
        )
        return f'{text} <img src="{media_url}" height="100%" width="100%"/>'

    def _scrape_image_for_recipe(self, recipe, slug):
        """Set the recipe cover from ``recipe.image_url``.

        The image is downloaded here, through the origin-checked downloader that
        also checks every redirect, and uploaded as bytes. Mealie is never asked
        to fetch the keyed URL itself, because its scraper follows redirects
        without that check.
        """
        if not recipe.image_url:
            return
        image_path = _recipe_path(slug) + "/image"
        if not is_allowed_image_url(recipe.image_url):
            logger.warning("Skipping cover image: not on an allowed image host")
            return
        image_bytes = download_image(recipe.image_url, follow_redirects=True)
        extension = Path(urlsplit(recipe.image_url).path).suffix.lstrip(".").lower()
        if extension not in COVER_IMAGE_EXTENSIONS:
            extension = "jpg"
        r = self.put(
            image_path,
            files={"image": (f"cover.{extension}", image_bytes)},
            data={"extension": extension},
        )
        r.raise_for_status()

    def _get_page(
        self, endpoint_name, page_num, per_page=50, *, timeout=DEFAULT_REQUEST_TIMEOUT
    ):
        r = self.get(
            f"/{endpoint_name}?page={page_num}&perPage={per_page}", timeout=timeout
        )
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            raise MealieInventoryError(
                "Invalid Mealie inventory: expected JSON"
            ) from None

    @staticmethod
    def _validate_page(payload):
        if not isinstance(payload, dict):
            raise MealieInventoryError(
                "Invalid Mealie inventory: expected a page object"
            )
        items = payload.get("items")
        total_pages = payload.get("total_pages")
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise MealieInventoryError(
                "Invalid Mealie inventory: expected a list of objects"
            )
        if type(total_pages) is not int or not 0 <= total_pages <= MAX_INVENTORY_PAGES:
            raise MealieInventoryError(
                "Invalid Mealie inventory: page count outside the supported budget"
            )
        if total_pages == 0 and items:
            raise MealieInventoryError(
                "Invalid Mealie inventory: items with zero pages"
            )
        return items, total_pages

    def _get_all_items(self, endpoint_name):
        all_items = []
        seen = set()
        total_pages = None
        total = None
        page_num = 1
        deadline = monotonic() + self.inventory_timeout
        while total_pages is None or page_num <= total_pages:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise MealieInventoryError(
                    "Incomplete Mealie inventory: acquisition deadline exceeded"
                )
            payload = self._get_page(
                endpoint_name,
                page_num,
                timeout=httpx.Timeout(
                    min(30.0, remaining), connect=min(10.0, remaining)
                ),
            )
            if monotonic() >= deadline:
                raise MealieInventoryError(
                    "Incomplete Mealie inventory: acquisition deadline exceeded"
                )
            items, pages = self._validate_page(payload)
            if total_pages is None:
                total_pages = pages
                total = payload.get("total")
            if pages != total_pages or payload.get("total") != total:
                raise MealieInventoryError(
                    "Incomplete Mealie inventory: pagination totals changed"
                )
            if "page" in payload and (
                type(payload["page"]) is not int or payload["page"] != page_num
            ):
                raise MealieInventoryError(
                    "Incomplete Mealie inventory: unexpected page number"
                )
            if total is not None and (
                type(total) is not int or not 0 <= total <= MAX_INVENTORY_ITEMS
            ):
                raise MealieInventoryError(
                    "Invalid Mealie inventory: item total outside the supported budget"
                )
            if not items and total_pages > 1:
                raise MealieInventoryError("Incomplete Mealie inventory: empty page")
            if len(all_items) + len(items) > MAX_INVENTORY_ITEMS:
                raise MealieInventoryError(
                    "Incomplete Mealie inventory: item budget exceeded"
                )
            for item in items:
                # IDs catch repeated entities even if their presentation changes.
                identity = next(
                    (
                        (key, str(item[key]))
                        for key in ("id", "slug", "name")
                        if item.get(key)
                    ),
                    None,
                )
                key = identity or ("payload", json.dumps(item, sort_keys=True))
                if key in seen:
                    raise MealieInventoryError(
                        "Incomplete Mealie inventory: repeated item"
                    )
                seen.add(key)
            all_items.extend(items)
            page_num += 1
        if total is not None and len(all_items) != total:
            raise MealieInventoryError(
                "Incomplete Mealie inventory: item total does not match"
            )
        if monotonic() >= deadline:
            raise MealieInventoryError(
                "Incomplete Mealie inventory: acquisition deadline exceeded"
            )
        return all_items

    def _create_item(self, endpoint_name, item):
        r = self.post(
            f"/{endpoint_name}",
            content=item.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        return r.json()

    def _load_organizer_index(
        self, endpoint_name, model_class, normalize_name: Callable[[str], str]
    ) -> dict[str, list[Any]]:
        existing_items = TypeAdapter(list[model_class]).validate_python(
            self._get_all_items(endpoint_name)
        )
        index: dict[str, list[Any]] = {}
        for item in existing_items:
            matches = index.setdefault(normalize_name(item.name), [])
            # The same proven identity may appear once per key; distinct IDs
            # for one key are kept so lookups can refuse to guess.
            if item.id is None or all(match.id != item.id for match in matches):
                matches.append(item)
        return index

    def _organizer_lookup_state(
        self, endpoint_name, model_class, normalize_name: Callable[[str], str]
    ) -> dict[str, list[Any]]:
        index = self._organizer_index.get(endpoint_name)
        if index is None:
            # Only a complete, validated inventory is cached.
            index = self._load_organizer_index(
                endpoint_name, model_class, normalize_name
            )
            self._organizer_index[endpoint_name] = index
        return index

    @staticmethod
    def _existing_organizer(index, endpoint_name, key: str, name: str):
        matches = index.get(key, [])
        if len(matches) > 1:
            label = ORGANIZER_LABELS.get(endpoint_name, endpoint_name)
            shown = name if len(name) <= 80 else name[:77] + "..."
            raise MealieOrganizerAmbiguityError(
                f"Ambiguous existing Mealie {label} {shown!r}: {len(matches)} "
                "entries with different IDs match. Merge or rename them in "
                "Mealie, then sync again."
            )
        return matches[0] if matches else None

    def _check_organizer_ambiguity(
        self,
        endpoint_name,
        model_class,
        items,
        normalize_name: Callable[[str], str] | None = None,
    ) -> None:
        normalize_name = normalize_name or _same_name
        index = self._organizer_lookup_state(endpoint_name, model_class, normalize_name)
        for item in items:
            self._existing_organizer(
                index, endpoint_name, normalize_name(item.name), item.name
            )

    def _create_item_name_to_item_lookup(
        self,
        endpoint_name,
        model_class,
        items,
        normalize_name: Callable[[str], str] | None = None,
    ):
        normalize_name = normalize_name or _same_name
        index = self._organizer_lookup_state(endpoint_name, model_class, normalize_name)
        resolved: dict[str, Any] = {}
        # Refuse ambiguity for every requested name before any create.
        for item in items:
            existing = self._existing_organizer(
                index, endpoint_name, normalize_name(item.name), item.name
            )
            if existing is not None:
                resolved[normalize_name(item.name)] = existing
        for item in items:
            key = normalize_name(item.name)
            if key in resolved:
                continue
            try:
                created_item = model_class(**self._create_item(endpoint_name, item))
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code != 409
                    or extract_mealie_detail_message(exc.response)
                    != "This item already exists."
                ):
                    raise
                refreshed = self._load_organizer_index(
                    endpoint_name, model_class, normalize_name
                )
                # The refresh is a complete inventory: it replaces the cache
                # even when this reconciliation fails, so later lookups see
                # any newly revealed ambiguity.
                index = refreshed
                self._organizer_index[endpoint_name] = index
                matches = refreshed.get(key, [])
                if len(matches) != 1 or matches[0].id is None:
                    raise
                created_item = matches[0]
            else:
                if normalize_name(created_item.name) != key:
                    # Never substitute a different organizer for the request.
                    self._organizer_index.pop(endpoint_name, None)
                    label = ORGANIZER_LABELS.get(endpoint_name, endpoint_name)
                    raise ValueError(
                        f"Mealie created a {label} with a different name than "
                        "requested; the recipe was not imported"
                    )
                if created_item.id is None:
                    # Do not cache an unproven identity; the next lookup rereads.
                    self._organizer_index.pop(endpoint_name, None)
                else:
                    index[key] = [created_item]
            resolved[key] = created_item
        return {item.name: resolved[normalize_name(item.name)] for item in items}

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

    def _check_recipe_organizers(self, recipe) -> None:
        """Refuse ambiguous existing units, foods or tags before any write."""
        for endpoint_name, model_class, attr_name in (
            ("units", RecipeUnit, "unit"),
            ("foods", RecipeFood, "food"),
        ):
            items = {
                getattr(ingredient, attr_name)
                for ingredient in recipe.recipe_ingredient
                if getattr(ingredient, attr_name) is not None
            }
            if items:
                self._check_organizer_ambiguity(endpoint_name, model_class, items)
        if recipe.tags:
            self._check_organizer_ambiguity(
                "organizers/tags", RecipeTag, set(recipe.tags), str.casefold
            )

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

    def _get_archive_endpoint(self) -> str:
        if self._archive_endpoint is None:
            response = self.get("/app/about")
            response.raise_for_status()
            version = str(response.json()["version"])
            # Mealie 2 and 3 renamed the legacy v1 create routes.
            match = re.match(r"v?(\d+)\.", version)
            if match is None:
                # Development builds may not report a release number. Assume a
                # current Mealie; a wrong route fails without creating anything.
                logger.warning(
                    "Unrecognized Mealie version %r; using the Mealie 2+ import route",
                    version,
                )
            self._archive_endpoint = (
                "/recipes/create-from-zip"
                if match is not None and int(match.group(1)) < 2
                else "/recipes/create/zip"
            )
        return self._archive_endpoint

    def _import_recipe_archive(self, recipe: RecipeWithImage) -> dict[str, Any]:
        # Native archives use snake_case, unlike the camelCase HTTP API. Omit
        # remote identity and media: Mealie assigns ownership, IDs and collisions.
        payload = recipe.model_dump(
            mode="json",
            exclude_none=True,
            exclude={
                "id": True,
                "user_id": True,
                "group_id": True,
                "slug": True,
                "image": True,
                "image_url": True,
                "recipe_instructions": {"__all__": {"image"}},
            },
        )
        archive_bytes = io.BytesIO()
        with ZipFile(archive_bytes, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("recipe.json", json.dumps(payload))
        response = self.post(
            self._get_archive_endpoint(),
            files={
                "archive": ("recipe.zip", archive_bytes.getvalue(), "application/zip")
            },
        )
        # Never retry or roll back an ambiguous import. Identity was present at
        # commit, so the next inventory pass can discover a completed recipe.
        response.raise_for_status()
        slug = response.json()
        response = self.get(_recipe_path(slug))
        response.raise_for_status()
        return response.json()

    def _attach_step_images(
        self, recipe: Recipe, stored: dict[str, Any], source: RecipeWithImage
    ) -> Recipe:
        instructions = stored.get(
            "recipeInstructions", stored.get("recipe_instructions", [])
        )
        source_steps = source.recipe_instructions or []
        if not any(step.image is not None for step in source_steps):
            return recipe
        source_texts = [step.text for step in source_steps]
        if (
            not isinstance(instructions, list)
            or len(instructions) != len(source_steps)
            or not all(
                isinstance(step, dict) and isinstance(step.get("text"), str)
                for step in instructions
            )
            or [step["text"] for step in instructions] != source_texts
            or len(set(source_texts)) != len(source_texts)
        ):
            logger.warning(
                "Skipping step images: imported instructions do not match source unambiguously"
            )
            return recipe
        changed = False
        for instruction, source_step in zip(instructions, source_steps):
            if source_step.image is None:
                continue
            try:
                asset = self.upload_asset(recipe.slug, source_step.image)
                # Keep server-assigned step IDs, references and other fields.
                instruction["text"] = self._build_recipestep_text(
                    recipe.id, instruction["text"], asset["fileName"]
                )
                changed = True
            except Exception as exc:
                logger.warning(
                    "Skipping step image for recipe %s (%s)",
                    recipe.slug,
                    _media_failure_reason(exc),
                )
        if changed:
            response = self.request(
                "PATCH",
                _recipe_path(recipe.slug),
                json={"recipeInstructions": instructions},
            )
            response.raise_for_status()
            return Recipe.model_validate(response.json())
        return recipe

    def create_recipe(self, recipe: RecipeWithImage) -> Recipe:
        self._get_archive_endpoint()
        recipe = recipe.model_copy(deep=True)
        self._check_recipe_organizers(recipe)
        recipe = self._update_item_ids(recipe, "units", RecipeUnit, "unit")
        recipe = self._update_item_ids(recipe, "foods", RecipeFood, "food")
        recipe = self._update_tag_ids(recipe)
        stored = self._import_recipe_archive(recipe)
        created = Recipe.model_validate(stored)
        _recipe_path(created.slug)
        for key in ("source", "kptncook_id"):
            if key in recipe.extras and created.extras.get(key) != recipe.extras[key]:
                raise ValueError(
                    f"Imported recipe {created.slug} is missing matching identity "
                    "extras; delete it in Mealie before the next sync to avoid "
                    "a duplicate"
                )
        try:
            self._scrape_image_for_recipe(recipe, created.slug)
        except Exception as exc:
            # The download error carries the keyed image URL: log no traceback,
            # message or URL, only the status or error type.
            logger.warning(
                "Skipping cover image for recipe %s (%s)",
                created.slug,
                _media_failure_reason(exc),
            )
        try:
            created = self._attach_step_images(created, stored, recipe)
        except Exception:
            logger.warning(
                "Skipping step image update for recipe %s", created.slug, exc_info=True
            )
        return created

    @staticmethod
    def validate_recipes(recipes):
        validated_recipes = []
        for position, recipe in enumerate(recipes, start=1):
            try:
                validated = Recipe.model_validate(recipe)
            except ValidationError:
                raise MealieInventoryError(
                    f"Incomplete Mealie inventory: invalid recipe at position {position}"
                ) from None
            try:
                _recipe_path(validated.slug)
            except ValueError:
                raise MealieInventoryError(
                    f"Incomplete Mealie inventory: unreadable identity at position {position}"
                ) from None
            if validated.extras.get("source") == "kptncook" and (
                not isinstance(validated.extras.get("kptncook_id"), str)
                or not validated.extras["kptncook_id"].strip()
            ):
                raise MealieInventoryError(
                    f"Incomplete Mealie inventory: unreadable identity at position {position}"
                )
            validated_recipes.append(validated)
        return validated_recipes

    def get_all_recipes(self):
        return self.validate_recipes(self._get_all_items("recipes"))

    def delete_via_slug(self, slug):
        r = self.delete(_recipe_path(slug))
        r.raise_for_status()
        return r.json() if r.content else None

    def get_via_slug(self, slug, *, timeout=httpx.USE_CLIENT_DEFAULT):
        r = self.get(_recipe_path(slug), timeout=timeout)
        r.raise_for_status()
        try:
            payload = r.json()
        except ValueError:
            raise MealieInventoryError(
                "Invalid Mealie inventory: expected recipe JSON"
            ) from None
        return self.validate_recipes([payload])[0]


def kptncook_to_mealie_ingredients(
    kptncook_ingredients: list[Ingredient] | None,
    servings: int = 1,
) -> tuple[list[RecipeIngredient], dict[str, list[UUID4]]]:
    mealie_ingredients = []
    ingredient_id_to_reference_ids: dict[str, list[UUID4]] = {}
    groups = iter_ingredient_groups(kptncook_ingredients or [])
    for group_label, ingredients in groups:
        for idx, ingredient in enumerate(ingredients):
            ingredient_title = (
                localized_fallback(
                    ingredient.ingredient.localized_title,
                    language=get_settings().kptncook_lang,
                )
                or ""
            )
            note = None
            if "," in ingredient_title:
                ingredient_title, note, *_ = (
                    p.strip() for p in ingredient_title.split(",")
                )
            food = RecipeFood(name=ingredient_title)
            quantity = scale_quantity(ingredient.quantity, servings)
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
        try:
            image: Image | None = step.image.get_image_with_api_key_url(api_key)
        except ImageOriginError:
            logger.warning("Skipping step image: not on an allowed image host")
            image = None
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
                text=get_step_text(step, language=get_settings().kptncook_lang),
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
        kcin.ingredients, servings=kcin.servings
    )
    author_comment = localized_fallback(
        kcin.author_comment, language=get_settings().kptncook_lang
    )
    kwargs = {
        "name": localized_fallback(
            kcin.localized_title, language=get_settings().kptncook_lang
        ),
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
        "recipe_yield": f"{kcin.servings} Portionen",
        "recipe_instructions": kptncook_to_mealie_steps(
            kcin.steps, api_key, ingredient_id_to_reference_ids
        ),
        "recipe_ingredient": ingredients,
        "image_url": kcin.get_image_url(api_key),
        "tags": kptncook_to_mealie_tags(kcin.active_tags),
        "extras": {"kptncook_id": kcin.id.oid, "source": "kptncook"},
    }
    logger.debug(
        "Converted recipe %s: %d ingredients, %d instructions",
        kcin.id.oid,
        len(ingredients),
        len(kwargs["recipe_instructions"]),
    )
    return RecipeWithImage(**kwargs)
