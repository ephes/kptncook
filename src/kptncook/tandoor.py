"""
Export recipes to Tandoor.

Tandoor expects a zip archive with a recipe.json file and an optional image.jpg.
"""

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import httpx

from kptncook.config import get_settings
from kptncook.exporter_utils import (
    asciify_string,
    bounded_export_filename,
    get_cover,
    get_step_text,
    move_to_free_target,
    write_zip,
    validate_export_id,
    ZipContent,
)
from kptncook.image_download import download_image
from kptncook.ingredient_groups import iter_ingredient_groups
from kptncook.models import (
    Ingredient,
    localized_fallback,
    Recipe,
    RecipeStep,
    StepIngredient,
    StepIngredientUnit,
    scale_quantity,
)

logger = logging.getLogger(__name__)


class TandoorExporter:
    def export(self, recipes: list[Recipe]) -> list[str]:
        for recipe in recipes:
            validate_export_id(recipe.id.oid)
        filenames = []
        for recipe in recipes:
            filenames.append(self.export_recipe(recipe=recipe))
        return filenames

    def export_recipe(self, recipe: Recipe) -> str:
        validate_export_id(recipe.id.oid)
        filename = self.get_export_filename(recipe=recipe)
        payload = self.get_recipe_payload(recipe=recipe)
        image_bytes = self.get_cover_image_bytes(recipe=recipe)
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / filename
            entries: list[tuple[str, ZipContent]] = [
                (
                    "recipe.json",
                    json.dumps(payload, ensure_ascii=False, indent=2),
                )
            ]
            if image_bytes is not None:
                entries.append(("image.jpg", image_bytes))
            write_zip(zip_path, entries)
            target = move_to_free_target(
                zip_path,
                Path.cwd(),
                stem=Path(filename).stem,
                extension=".zip",
                disambiguator=recipe.id.oid,
            )
        return target.name

    def get_export_filename(self, recipe: Recipe) -> str:
        """Return the preferred file name.

        The actual name may get the recipe id appended if a file with this
        name already exists (for example a second recipe with the same title).
        """
        title = (
            localized_fallback(
                recipe.localized_title, language=get_settings().kptncook_lang
            )
            or "kptncook-recipe"
        )
        return bounded_export_filename(asciify_string(title), ".zip")

    def get_cover_image_bytes(self, recipe: Recipe) -> bytes | None:
        cover = get_cover(image_list=recipe.image_list)
        if cover is None:
            return None
        cover_url = recipe.get_image_url(api_key=get_settings().kptncook_api_key)
        if cover_url is None:
            return None
        try:
            return download_image(cover_url, follow_redirects=True)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.info(
                    'Cover image for "%s" not found online any more.',
                    localized_fallback(
                        recipe.localized_title, language=get_settings().kptncook_lang
                    )
                    or "kptncook-recipe",
                )
            else:
                logger.warning(
                    "While trying to fetch the cover img a HTTP error occurred: %s",
                    exc.response.status_code,
                )
            return None
        except httpx.RequestError:
            logger.warning(
                "Skipping cover image: download failed or exceeded limits",
            )
            return None

    def get_recipe_payload(self, recipe: Recipe) -> dict[str, Any]:
        return {
            "name": localized_fallback(
                recipe.localized_title, language=get_settings().kptncook_lang
            )
            or "",
            "description": localized_fallback(
                recipe.author_comment, language=get_settings().kptncook_lang
            )
            or "",
            "servings": recipe.servings,
            "source_url": self.get_source_url(recipe=recipe),
            "working_time": recipe.preparation_time,
            "waiting_time": recipe.cooking_time,
            "keywords": self.get_keywords(recipe=recipe),
            "steps": self.get_steps(recipe=recipe),
            "ingredients": self.get_ingredients(recipe=recipe),
        }

    def get_source_url(self, recipe: Recipe) -> str:
        source_id = recipe.uid or recipe.id.oid
        return f"https://share.kptncook.com/{source_id}"

    def get_keywords(self, recipe: Recipe) -> list[dict[str, str]]:
        keywords = [{"name": "kptncook"}]
        if recipe.active_tags:
            keywords.extend(
                [{"name": tag} for tag in self._filter_active_tags(recipe.active_tags)]
            )
        if recipe.rtype:
            keywords.append({"name": recipe.rtype})
        return keywords

    @staticmethod
    def _filter_active_tags(active_tags: list[str]) -> list[str]:
        return [tag for tag in active_tags if tag != "kptncook"]

    def get_steps(self, recipe: Recipe) -> list[dict[str, Any]]:
        steps = []
        for step in recipe.steps:
            steps.append(
                {
                    "instruction": get_step_text(
                        step, language=get_settings().kptncook_lang, markup=True
                    ),
                    "ingredients": self.get_step_ingredients(
                        step=step, servings=recipe.servings
                    ),
                }
            )
        return steps

    def get_step_ingredients(
        self, step: RecipeStep, servings: int = 1
    ) -> list[dict[str, Any]]:
        ingredients = []
        for ingredient in step.ingredients or []:
            payload = self.get_step_ingredient_payload(
                ingredient=ingredient, servings=servings
            )
            if payload is not None:
                ingredients.append(payload)
        return ingredients

    def get_step_ingredient_payload(
        self, ingredient: StepIngredient | None, servings: int = 1
    ) -> dict[str, Any] | None:
        if ingredient is None:
            return None
        ingredient_name = self.get_step_ingredient_name(ingredient=ingredient)
        if not ingredient_name:
            logger.debug(
                "Skipping Tandoor step ingredient without a resolvable food name."
            )
            return None
        payload = {
            "amount": scale_quantity(ingredient.quantity, servings),
            "unit": self.get_step_unit_payload(unit=ingredient.unit),
            "food": {"name": ingredient_name},
        }
        return {key: value for key, value in payload.items() if value is not None}

    def get_step_ingredient_name(self, ingredient: StepIngredient) -> str | None:
        details = ingredient.ingredient
        if details is None:
            return None
        if details.uncountable_title:
            title = localized_fallback(
                details.uncountable_title, language=get_settings().kptncook_lang
            )
            if title:
                return title
        if details.localized_title:
            title = localized_fallback(
                details.localized_title, language=get_settings().kptncook_lang
            )
            if title:
                return title
        if details.number_title:
            title = localized_fallback(
                details.number_title, language=get_settings().kptncook_lang
            )
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
            localized_fallback(
                unit.localized_title, language=get_settings().kptncook_lang
            ),
            localized_fallback(unit.short_title, language=get_settings().kptncook_lang),
        ):
            if candidate:
                return {"name": candidate}
        return None

    def get_ingredients(self, recipe: Recipe) -> list[dict[str, Any]]:
        ingredients = []
        for group_label, group_ingredients in iter_ingredient_groups(
            recipe.ingredients
        ):
            if group_label:
                ingredients.append(self.get_group_header_payload(label=group_label))
            for ingredient in group_ingredients:
                ingredients.append(
                    self.get_ingredient_payload(
                        ingredient=ingredient, servings=recipe.servings
                    )
                )
        return ingredients

    def get_group_header_payload(self, label: str) -> dict[str, Any]:
        return {"note": label, "is_header": True, "no_amount": True, "amount": 0}

    def get_ingredient_payload(
        self, ingredient: Ingredient, servings: int = 1
    ) -> dict[str, Any]:
        ingredient_name = self.get_ingredient_name(ingredient=ingredient) or ""
        payload = {
            "amount": scale_quantity(ingredient.quantity, servings),
            "unit": self.get_unit_payload(measure=ingredient.measure),
            "food": {"name": ingredient_name},
        }
        return {key: value for key, value in payload.items() if value is not None}

    def get_ingredient_name(self, ingredient: Ingredient) -> str | None:
        details = ingredient.ingredient
        if details.uncountable_title is not None:
            title = localized_fallback(
                details.uncountable_title, language=get_settings().kptncook_lang
            )
            if title:
                return title
        return localized_fallback(
            details.localized_title, language=get_settings().kptncook_lang
        )

    def get_unit_payload(self, measure: str | None) -> dict[str, str] | None:
        if measure is None:
            return None
        return {"name": measure}
