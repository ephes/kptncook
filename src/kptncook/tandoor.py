"""
Export recipes to Tandoor.

Tandoor's Default importer expects an uploaded zip whose entries are .zip files
(each entry = one recipe zip with recipe.json and optional image). By default
we produce a single outer zip containing one inner recipe.zip per recipe.
"""

import json
import logging
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from kptncook.config import get_settings
from kptncook.exporter_utils import (
    asciify_string,
    get_cover,
    get_step_text,
    move_to_target_dir,
    write_zip,
)
from kptncook.ingredient_groups import iter_ingredient_groups
from kptncook.models import (
    Ingredient,
    localized_fallback,
    Recipe,
    RecipeStep,
    StepIngredient,
    StepIngredientUnit,
)

logger = logging.getLogger(__name__)
IMAGE_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

TANDOOR_BULK_EXPORT_FILENAME = "kptncook-tandoor-export.zip"

MAX_IMAGE_FETCH_WORKERS = 10


class TandoorExporter:
    def export(self, recipes: list[Recipe]) -> list[str]:
        """Export all recipes into a single zip (Tandoor Default importer format)."""
        if not recipes:
            return []
        payloads_and_filenames = [
            (
                self.get_recipe_payload(recipe=recipe),
                self.get_export_filename(recipe=recipe),
            )
            for recipe in recipes
        ]
        with ThreadPoolExecutor(max_workers=MAX_IMAGE_FETCH_WORKERS) as executor:
            image_bytes_list = list(executor.map(self.get_cover_image_bytes, recipes))
        entries: list[tuple[str, bytes]] = []
        for (payload, inner_filename), image_bytes in zip(
            payloads_and_filenames, image_bytes_list
        ):
            inner_zip_bytes = self._build_recipe_zip(payload, image_bytes)
            entries.append((inner_filename, inner_zip_bytes))
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / TANDOOR_BULK_EXPORT_FILENAME
            write_zip(zip_path, entries)
            move_to_target_dir(zip_path, Path.cwd() / TANDOOR_BULK_EXPORT_FILENAME)
        return [TANDOOR_BULK_EXPORT_FILENAME]

    def export_recipe(self, recipe: Recipe) -> str:
        """Export a single recipe to a zip (one outer zip, one inner recipe.zip)."""
        filename = self.get_export_filename(recipe=recipe)
        payload = self.get_recipe_payload(recipe=recipe)
        image_bytes = self.get_cover_image_bytes(recipe=recipe)
        inner_zip_bytes = self._build_recipe_zip(payload, image_bytes)
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / filename
            write_zip(zip_path, [("recipe.zip", inner_zip_bytes)])
            move_to_target_dir(zip_path, Path.cwd() / filename)
        return filename

    def _build_recipe_zip(
        self, payload: dict[str, Any], image_bytes: bytes | None
    ) -> bytes:
        """Build the inner recipe zip (recipe.json + optional image.jpg)."""
        buffer = BytesIO()
        with zipfile.ZipFile(
            buffer, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zf:
            zf.writestr(
                "recipe.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
            if image_bytes is not None:
                zf.writestr("image.jpg", image_bytes)
        return buffer.getvalue()

    def get_export_filename(self, recipe: Recipe) -> str:
        title = localized_fallback(recipe.localized_title) or "kptncook-recipe"
        return f"{asciify_string(title)}.zip"

    def get_cover_image_bytes(self, recipe: Recipe) -> bytes | None:
        cover = get_cover(image_list=recipe.image_list)
        if cover is None:
            return None
        cover_url = recipe.get_image_url(api_key=get_settings().kptncook_api_key)
        if cover_url is None:
            return None
        try:
            response = httpx.get(
                cover_url,
                follow_redirects=True,
                timeout=IMAGE_DOWNLOAD_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.info(
                    'Cover image for "%s" not found online any more.',
                    localized_fallback(recipe.localized_title) or "kptncook-recipe",
                )
            else:
                logger.warning(
                    "While trying to fetch the cover img a HTTP error occurred: %s: %s",
                    exc.response.status_code,
                    exc,
                )
            return None
        except httpx.RequestError as exc:
            logger.warning(
                "While trying to fetch the cover img a network error occurred: %s",
                exc,
            )
            return None
        return response.content

    def get_recipe_payload(self, recipe: Recipe) -> dict[str, Any]:
        """Payload must match Tandoor RecipeExportSerializer (name, description, keywords, steps, working_time, waiting_time, internal, nutrition, servings, servings_text, source_url)."""
        return {
            "name": localized_fallback(recipe.localized_title) or "",
            "description": localized_fallback(recipe.author_comment) or "",
            "keywords": self.get_keywords_export(recipe=recipe),
            "steps": self.get_steps(recipe=recipe),
            "working_time": recipe.preparation_time,
            "waiting_time": recipe.cooking_time or 0,
            "internal": True,
            "nutrition": None,
            "servings": 3,
            "servings_text": "",
            "source_url": self.get_source_url(recipe=recipe),
        }

    def get_source_url(self, recipe: Recipe) -> str:
        source_id = recipe.uid or recipe.id.oid
        return f"https://share.kptncook.com/{source_id}"

    def get_keywords(self, recipe: Recipe) -> list[str]:
        keywords = ["kptncook"]
        if recipe.active_tags:
            keywords.extend(self._filter_active_tags(recipe.active_tags))
        if recipe.rtype:
            keywords.append(recipe.rtype)
        return keywords

    def get_keywords_export(self, recipe: Recipe) -> list[dict[str, Any]]:
        """KeywordExportSerializer expects list of {name, description, created_at?, updated_at?}."""
        return [
            {"name": tag, "description": ""} for tag in self.get_keywords(recipe=recipe)
        ]

    @staticmethod
    def _filter_active_tags(active_tags: list[str]) -> list[str]:
        return [tag for tag in active_tags if tag != "kptncook"]

    def get_steps(self, recipe: Recipe) -> list[dict[str, Any]]:
        """StepExportSerializer expects name, instruction, ingredients, time, order, show_as_header, show_ingredients_table."""
        steps = []
        order = 0
        recipe_ingredients = self.get_recipe_ingredients_as_step_ingredients(
            recipe=recipe
        )
        if recipe_ingredients:
            steps.append(
                {
                    "name": "",
                    "instruction": "",
                    "ingredients": recipe_ingredients,
                    "time": 0,
                    "order": order,
                    "show_as_header": True,
                    "show_ingredients_table": True,
                }
            )
            order += 1
        for step in recipe.steps:
            steps.append(
                {
                    "name": "",
                    "instruction": get_step_text(step),
                    "ingredients": self.get_step_ingredients(step=step),
                    "time": 0,
                    "order": order,
                    "show_as_header": True,
                    "show_ingredients_table": True,
                }
            )
            order += 1
        return steps

    def get_recipe_ingredients_as_step_ingredients(
        self, recipe: Recipe
    ) -> list[dict[str, Any]]:
        """Recipe-level ingredients as step-ingredient payloads (IngredientExportSerializer shape)."""
        result = []
        for group_label, group_ingredients in iter_ingredient_groups(
            recipe.ingredients
        ):
            if group_label:
                result.append(
                    {
                        "food": self._food_export(""),
                        "unit": None,
                        "amount": 0,
                        "note": group_label,
                        "order": 0,
                        "is_header": True,
                        "no_amount": True,
                        "always_use_plural_unit": False,
                        "always_use_plural_food": False,
                    }
                )
            for ingredient in group_ingredients:
                result.append(self._recipe_ingredient_to_step_payload(ingredient))
        return result

    def _recipe_ingredient_to_step_payload(
        self, ingredient: Ingredient
    ) -> dict[str, Any]:
        """Single recipe-level ingredient as step-ingredient payload."""
        name = self.get_ingredient_name(ingredient=ingredient) or ""
        amount = ingredient.quantity if ingredient.quantity is not None else 0.0
        unit_raw = self.get_unit_payload(measure=ingredient.measure)
        unit: dict[str, Any] | None = None
        if unit_raw and unit_raw.get("name"):
            unit = {
                "name": unit_raw["name"],
                "plural_name": unit_raw["name"],
                "description": None,
            }
        return {
            "food": self._food_export(name),
            "unit": unit,
            "amount": amount,
            "note": "",
            "order": 0,
            "is_header": False,
            "no_amount": False,
            "always_use_plural_unit": False,
            "always_use_plural_food": False,
        }

    def get_step_ingredients(self, step: RecipeStep) -> list[dict[str, Any]]:
        ingredients = []
        for ingredient in step.ingredients or []:
            payload = self.get_step_ingredient_payload(ingredient=ingredient)
            if payload is not None:
                ingredients.append(payload)
        return ingredients

    def get_step_ingredient_payload(
        self, ingredient: StepIngredient | None
    ) -> dict[str, Any] | None:
        if ingredient is None:
            return None
        ingredient_name = self.get_step_ingredient_name(ingredient=ingredient) or ""
        if not ingredient_name.strip():
            logger.debug(
                "Skipping Tandoor step ingredient without a resolvable food name."
            )
            return None
        unit = self.get_step_unit_payload(unit=ingredient.unit)
        amount = ingredient.quantity if ingredient.quantity is not None else 0.0
        return {
            "food": self._food_export(name=ingredient_name),
            "unit": unit,
            "amount": amount,
            "note": "",
            "order": 0,
            "is_header": False,
            "no_amount": False,
            "always_use_plural_unit": False,
            "always_use_plural_food": False,
        }

    def get_step_ingredient_name(self, ingredient: StepIngredient) -> str | None:
        details = ingredient.ingredient
        if details is None:
            return None
        if details.uncountable_title:
            title = localized_fallback(details.uncountable_title)
            if title:
                return title
        if details.localized_title:
            title = localized_fallback(details.localized_title)
            if title:
                return title
        if details.number_title:
            title = localized_fallback(details.number_title)
            if title:
                return title
        return None

    def get_step_unit_payload(
        self, unit: StepIngredientUnit | None
    ) -> dict[str, Any] | None:
        """UnitExportSerializer: name, plural_name, description."""
        if unit is None:
            return None
        for candidate in (
            unit.name,
            localized_fallback(unit.localized_title),
            localized_fallback(unit.short_title),
        ):
            if candidate:
                return {
                    "name": candidate,
                    "plural_name": candidate,
                    "description": None,
                }
        return None

    @staticmethod
    def _food_export(name: str) -> dict[str, Any]:
        """FoodExportSerializer: name, plural_name, ignore_shopping, supermarket_category."""
        return {
            "name": name,
            "plural_name": None,
            "ignore_shopping": False,
            "supermarket_category": None,
        }

    def get_ingredients(self, recipe: Recipe) -> list[dict[str, Any]]:
        ingredients = []
        for group_label, group_ingredients in iter_ingredient_groups(
            recipe.ingredients
        ):
            if group_label:
                ingredients.append(self.get_group_header_payload(label=group_label))
            for ingredient in group_ingredients:
                ingredients.append(self.get_ingredient_payload(ingredient=ingredient))
        return ingredients

    def get_group_header_payload(self, label: str) -> dict[str, Any]:
        return {"note": label, "is_header": True, "no_amount": True, "amount": 0}

    def get_ingredient_payload(self, ingredient: Ingredient) -> dict[str, Any]:
        ingredient_name = self.get_ingredient_name(ingredient=ingredient) or ""
        payload = {
            "amount": ingredient.quantity,
            "unit": self.get_unit_payload(measure=ingredient.measure),
            "food": {"name": ingredient_name},
        }
        return {key: value for key, value in payload.items() if value is not None}

    def get_ingredient_name(self, ingredient: Ingredient) -> str | None:
        details = ingredient.ingredient
        if details.uncountable_title is not None:
            title = localized_fallback(details.uncountable_title)
            if title:
                return title
        return localized_fallback(details.localized_title)

    def get_unit_payload(self, measure: str | None) -> dict[str, str] | None:
        if measure is None:
            return None
        return {"name": measure}
