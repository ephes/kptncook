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
import re
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Template

from kptncook.config import settings
from kptncook.exporter_utils import (
    asciify_string,
    get_cover,
    move_to_target_dir,
    write_zip,
)
from kptncook.ingredient_groups import iter_ingredient_groups
from kptncook.models import Image, Ingredient, Recipe, localized_fallback

PAPRIKA_RECIPE_TEMPLATE = """{
   "uid":"{{recipe.id.oid}}",
   "name":"{{localized_fallback(recipe.localized_title)|default('',true)}}",
   "directions": "{% for step in recipe.steps %}{{localized_fallback(step.title)|default('',true)}}\\n{% endfor %}",
   "servings":"2",
   "rating":0,
   "difficulty":"",
   "ingredients":"{{ingredients_text}}",
   "notes":"",
   "created":"{{dtnow}}",
   "image_url":null,
   "cook_time":"{{recipe.cooking_time|default('',true)}}",
   "prep_time":"{{recipe.preparation_time|default('',true)}}",
   "source":"Kptncook",
   "source_url":"",
   "hash" : "{{hash}}",
   "photo_hash":null,
   "photos":[],
   "photo": "{{cover_filename}}",
   "nutritional_info":"{% for nutrient, amount in recipe.recipe_nutrition %}{{nutrient}}: {{amount}}\\n{% endfor %}",
   "photo_data":"{{cover_img}}",
   "photo_large":null,
   "categories":["Kptncook"]
}
"""  # noqa: E501

logger = logging.getLogger(__name__)


class GeneratedData:
    def __init__(
        self, cover_filename: str | None, cover_img: str | None, dtnow: str, hash_: str
    ):
        self.cover_filename = cover_filename
        self.cover_img = cover_img
        self.dtnow = dtnow
        self.hash = hash_


class PaprikaExporter:
    invalid_control_chars = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F-\x9F]")
    template = Template(PAPRIKA_RECIPE_TEMPLATE, trim_blocks=True)
    unescaped_newline = re.compile(r"(?<!\\)\n")

    def export(self, recipes: list[Recipe]) -> str:
        export_data = self.get_export_data(recipes=recipes)
        filename = self.get_export_filename(export_data=export_data, recipes=recipes)
        tmp_dir = tempfile.mkdtemp()
        filename_full_path = self.save_recipes(
            export_data=export_data, directory=tmp_dir, filename=filename
        )
        move_to_target_dir(
            source=filename_full_path, target=os.path.join(str(Path.cwd()), filename)
        )
        return filename

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
        ingredients_text = self.get_ingredients_text(recipe.ingredients)
        recipe_as_json = self.template.render(
            recipe=recipe,
            localized_fallback=localized_fallback,
            dtnow=generated.dtnow,
            cover_filename=generated.cover_filename,
            hash=generated.hash,
            cover_img=generated.cover_img,
            ingredients_text=ingredients_text,
        )
        recipe_as_json = self.invalid_control_chars.sub("", recipe_as_json)
        recipe_as_json = self.unescaped_newline.sub(" ", recipe_as_json)
        json.loads(recipe_as_json)  # check if valid json
        return recipe_as_json

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
        return "\\n".join(lines) + "\\n"

    def format_ingredient_line(self, ingredient: Ingredient) -> str:
        parts: list[str] = []
        if ingredient.quantity:
            parts.append("{0:g}".format(ingredient.quantity))
        if ingredient.measure:
            parts.append(ingredient.measure)
        ingredient_name = (
            localized_fallback(ingredient.ingredient.uncountable_title) or ""
        )
        if ingredient_name:
            parts.append(ingredient_name)
        return " ".join(part for part in parts if part).strip()

    def get_export_data(self, recipes: list[Recipe]) -> dict[str, str]:
        export_data = dict()
        for recipe in recipes:
            try:
                recipe_as_json = self.get_recipe_as_json_string(recipe=recipe)
                export_data[str(recipe.id.oid)] = recipe_as_json
            except json.JSONDecodeError as e:
                logger.warning("Could not parse recipe %s: %s", recipe.id.oid, e)
        return export_data

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
        cover_url = recipe.get_image_url(api_key=settings.kptncook_api_key)
        if not isinstance(cover_url, str):
            raise ValueError("Cover URL must be a string")
        try:
            response = httpx.get(cover_url)
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
