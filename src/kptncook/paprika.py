"""
Export a single recipe to Paprika App

file format:
    1. export recipe to json
    2. compress file as gz: naming convention: a_recipe_name.paprikarecipe (Singular)
    3. zip this file as some_recipes.paprikarecipes (Plural!)
"""

import base64
import glob
import gzip
import json
import logging
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from kptncook.config import get_settings
from kptncook.exporter_utils import (
    asciify_string,
    get_cover,
    get_step_text,
    move_to_free_target,
    write_zip,
)
from kptncook.ingredient_groups import iter_ingredient_groups
from kptncook.models import Image, Ingredient, Recipe, localized_fallback

logger = logging.getLogger(__name__)
IMAGE_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class GeneratedData:
    def __init__(
        self, cover_filename: str | None, cover_img: str | None, dtnow: str, hash_: str
    ):
        self.cover_filename = cover_filename
        self.cover_img = cover_img
        self.dtnow = dtnow
        self.hash = hash_


class PaprikaExporter:
    def export(self, recipes: list[Recipe]) -> str:
        export_data = self.get_export_data(recipes=recipes)
        filename = self.get_export_filename(export_data=export_data, recipes=recipes)
        with tempfile.TemporaryDirectory() as tmp_dir:
            filename_full_path = self.save_recipes(
                export_data=export_data, directory=tmp_dir, filename=filename
            )
            # never overwrite an existing export; pick a free name instead
            disambiguator = recipes[0].id.oid if len(export_data) == 1 else None
            target = move_to_free_target(
                source=filename_full_path,
                directory=Path.cwd(),
                stem=Path(filename).stem,
                extension=Path(filename).suffix,
                disambiguator=disambiguator,
            )
            return target.name

    def get_export_filename(
        self, export_data: dict[str, str], recipes: list[Recipe]
    ) -> str:
        if len(export_data) == 1:
            return (
                asciify_string(
                    s=localized_fallback(recipes[0].localized_title) or "recipe"
                )
                + ".paprikarecipes"
            )
        else:
            return "allrecipes.paprikarecipes"

    def get_generated_data(self, recipe: Recipe) -> GeneratedData:
        """Just to make testing easier and only have one method to mock in tests."""
        cover_filename, cover_img = self.get_cover_img_as_base64_string(recipe=recipe)
        dtnow = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        hash = secrets.token_hex(32)
        return GeneratedData(cover_filename, cover_img, dtnow, hash)

    def get_recipe_as_json_string(self, recipe: Recipe) -> str:
        generated = self.get_generated_data(recipe=recipe)
        document = {
            "uid": str(recipe.id.oid),
            "name": localized_fallback(recipe.localized_title) or "",
            "directions": "".join(
                (get_step_text(step) or "") + "\n" for step in recipe.steps
            ),
            "servings": "2",
            "rating": 0,
            "difficulty": "",
            "ingredients": self.get_ingredients_text(recipe.ingredients),
            "notes": "",
            "created": generated.dtnow,
            "image_url": None,
            "cook_time": str(recipe.cooking_time or ""),
            "prep_time": str(recipe.preparation_time or ""),
            "source": "Kptncook",
            "source_url": "",
            "hash": generated.hash,
            "photo_hash": None,
            "photos": [],
            "photo": generated.cover_filename or "",
            "nutritional_info": "".join(
                f"{nutrient}: {amount}\n"
                for nutrient, amount in recipe.recipe_nutrition
            ),
            "photo_data": generated.cover_img or "",
            "photo_large": None,
            "categories": ["Kptncook"],
        }
        return json.dumps(document, ensure_ascii=False)

    def get_ingredients_text(self, ingredients: list[Ingredient]) -> str:
        lines = []
        for group_label, group_ingredients in iter_ingredient_groups(ingredients):
            if group_label:
                lines.append(f"{group_label}:")
            for ingredient in group_ingredients:
                line = self.format_ingredient_line(ingredient)
                if line:
                    lines.append(line)
        if not lines:
            return ""
        return "\n".join(lines) + "\n"

    def format_ingredient_line(self, ingredient: Ingredient) -> str:
        parts: list[str] = []
        if ingredient.quantity:
            parts.append(f"{ingredient.quantity:g}")
        if ingredient.measure:
            parts.append(ingredient.measure)
        ingredient_name = (
            localized_fallback(ingredient.ingredient.uncountable_title) or ""
        )
        if ingredient_name:
            parts.append(ingredient_name)
        return " ".join(part for part in parts if part).strip()

    def get_export_data(self, recipes: list[Recipe]) -> dict[str, str]:
        return {
            str(recipe.id.oid): self.get_recipe_as_json_string(recipe)
            for recipe in recipes
        }

    def save_recipes(
        self, export_data: dict[str, Any], filename: str, directory: str
    ) -> str:
        for id, recipe_as_json in export_data.items():
            recipe_as_gz = os.path.join(directory, "recipe_" + id + ".paprikarecipe")
            with gzip.open(recipe_as_gz, "wb") as f:
                f.write(recipe_as_json.encode("utf-8"))
        filename_full_path = os.path.join(directory, filename)
        gz_files = glob.glob(os.path.join(directory, "*.paprikarecipe"))
        logger.debug("Paprika export files: %s", gz_files)
        entries = [(Path(gz_file).name, Path(gz_file)) for gz_file in gz_files]
        write_zip(Path(filename_full_path), entries)
        return filename_full_path

    def get_cover_img_as_base64_string(
        self, recipe: Recipe
    ) -> tuple[str | None, str | None]:
        cover = get_cover(image_list=recipe.image_list)
        if cover is None:
            raise ValueError("No cover image found")
        cover_url = recipe.get_image_url(api_key=get_settings().kptncook_api_key)
        if not isinstance(cover_url, str):
            raise ValueError("Cover URL must be a string")
        try:
            response = httpx.get(cover_url, timeout=IMAGE_DOWNLOAD_TIMEOUT)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                title = localized_fallback(recipe.localized_title) or "kptncook-recipe"
                logger.warning('Cover image for "%s" not found online any more.', title)
            else:
                logger.error(
                    "HTTP error while fetching cover image (%s): %s",
                    exc.response.status_code,
                    exc,
                )
            return None, None
        return cover.name, base64.b64encode(response.content).decode("utf-8")

    def asciify_string(self, s: str) -> str:
        return asciify_string(s)

    def get_cover(self, image_list: list[Image] | None) -> Image | None:
        if not isinstance(image_list, list):
            raise ValueError("Parameter image_list must be a list")
        return get_cover(image_list)
