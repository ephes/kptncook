"""
Export recipes to Tandoor.

Tandoor expects a zip archive with a recipe.json file and an optional image.jpg.
"""

import json
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx
from unidecode import unidecode

from kptncook.config import settings
from kptncook.models import (
    Image,
    Ingredient,
    localized_fallback,
    Recipe,
    RecipeStep,
    StepIngredient,
    StepIngredientUnit,
)

logger = logging.getLogger(__name__)


class TandoorExporter:
    def export(self, recipes: list[Recipe]) -> list[str]:
        filenames = []
        for recipe in recipes:
            filenames.append(self.export_recipe(recipe=recipe))
        return filenames

    def export_recipe(self, recipe: Recipe) -> str:
        filename = self.get_export_filename(recipe=recipe)
        payload = self.get_recipe_payload(recipe=recipe)
        image_bytes = self.get_cover_image_bytes(recipe=recipe)
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / filename
            with zipfile.ZipFile(
                zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as zip_file:
                zip_file.writestr(
                    "recipe.json", json.dumps(payload, ensure_ascii=False, indent=2)
                )
                if image_bytes is not None:
                    zip_file.writestr("image.jpg", image_bytes)
            shutil.move(zip_path, Path.cwd() / filename)
        return filename

    def get_export_filename(self, recipe: Recipe) -> str:
        title = localized_fallback(recipe.localized_title) or "kptncook-recipe"
        return f"{self.asciify_string(title)}.zip"

    def asciify_string(self, s: str) -> str:
        s = unidecode(s)
        s = re.sub(r"[^\w\s]", "_", s)
        s = re.sub(r"\s+", "_", s)
        return s

    def get_cover_image_bytes(self, recipe: Recipe) -> bytes | None:
        cover = self.get_cover(image_list=recipe.image_list)
        if cover is None:
            return None
        cover_url = recipe.get_image_url(api_key=settings.kptncook_api_key)
        if cover_url is None:
            return None
        try:
            response = httpx.get(cover_url, follow_redirects=True)
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

    def get_cover(self, image_list: list[Image] | None) -> Image | None:
        if not isinstance(image_list, list):
            return None
        try:
            [cover] = [i for i in image_list if i.type == "cover"]
        except ValueError:
            return None
        return cover

    def get_recipe_payload(self, recipe: Recipe) -> dict[str, Any]:
        return {
            "name": localized_fallback(recipe.localized_title) or "",
            "description": localized_fallback(recipe.author_comment) or "",
            "servings": 3,
            "source_url": self.get_source_url(recipe=recipe),
            "prep_time": recipe.preparation_time,
            "cook_time": recipe.cooking_time,
            "keywords": self.get_keywords(recipe=recipe),
            "steps": self.get_steps(recipe=recipe),
            "ingredients": self.get_ingredients(recipe=recipe),
        }

    def get_source_url(self, recipe: Recipe) -> str:
        source_id = recipe.uid or recipe.id.oid
        return f"https://share.kptncook.com/{source_id}"

    def get_keywords(self, recipe: Recipe) -> list[str]:
        keywords = ["kptncook"]
        if recipe.rtype:
            keywords.append(recipe.rtype)
        return keywords

    def get_steps(self, recipe: Recipe) -> list[dict[str, Any]]:
        steps = []
        for step in recipe.steps:
            steps.append(
                {
                    "instruction": localized_fallback(step.title) or "",
                    "ingredients": self.get_step_ingredients(step=step),
                }
            )
        return steps

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
        payload = {
            "amount": ingredient.quantity,
            "unit": self.get_step_unit_payload(unit=ingredient.unit),
            "food": {"name": ingredient_name},
        }
        return {key: value for key, value in payload.items() if value is not None}

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
    ) -> dict[str, str] | None:
        if unit is None:
            return None
        for candidate in (
            unit.name,
            localized_fallback(unit.localized_title),
            localized_fallback(unit.short_title),
        ):
            if candidate:
                return {"name": candidate}
        return None

    def get_ingredients(self, recipe: Recipe) -> list[dict[str, Any]]:
        ingredients = []
        for ingredient in recipe.ingredients:
            ingredients.append(self.get_ingredient_payload(ingredient=ingredient))
        return ingredients

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
