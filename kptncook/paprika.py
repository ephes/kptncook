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
import os
import re
import secrets
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Template
from unidecode import unidecode

from kptncook.config import settings
from kptncook.models import Image, Recipe

PAPRIKA_RECIPE_TEMPLATE = """{
   "uid":"{{recipe.id.oid}}",
   "name":"{{recipe.localized_title.de}}",
   "directions": "{% for step in recipe.steps %}{{step.title.de}}\\n{% endfor %}",
   "servings":"2",
   "rating":0,
   "difficulty":"",
   "ingredients":"{% for ingredient in recipe.ingredients %}{% if ingredient.quantity %}{{'{0:g}'.format(ingredient.quantity) }}{% endif %} {{ingredient.measure|default('',true)}} {{ingredient.ingredient.uncountable_title.de|default('',true)}}\\n{% endfor %}",
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
        self.move_to_target_dir(
            source=filename_full_path, target=os.path.join(str(Path.cwd()), filename)
        )
        return filename

    def get_export_filename(
        self, export_data: dict[str, str], recipes: list[Recipe]
    ) -> str:
        if len(export_data) == 1:
            return (
                self.asciify_string(s=recipes[0].localized_title.de) + ".paprikarecipes"
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
        recipe_as_json = self.template.render(
            recipe=recipe,
            dtnow=generated.dtnow,
            cover_filename=generated.cover_filename,
            hash=generated.hash,
            cover_img=generated.cover_img,
        )
        recipe_as_json = self.invalid_control_chars.sub("", recipe_as_json)
        recipe_as_json = self.unescaped_newline.sub(" ", recipe_as_json)
        json.loads(recipe_as_json)  # check if valid json
        return recipe_as_json

    def get_export_data(self, recipes: list[Recipe]) -> dict[str, str]:
        export_data = dict()
        for recipe in recipes:
            try:
                recipe_as_json = self.get_recipe_as_json_string(recipe=recipe)
                export_data[str(recipe.id.oid)] = recipe_as_json
            except json.JSONDecodeError as e:
                print(f"Could not parse recipe {recipe.id.oid}: {e}")
        return export_data

    def move_to_target_dir(self, source: str, target: str) -> str:
        return shutil.move(source, target)

    def asciify_string(self, s) -> str:
        s = unidecode(s)
        s = re.sub(r"[^\w\s]", "_", s)
        s = re.sub(r"\s+", "_", s)
        return s

    def save_recipes(
        self, export_data: dict[str, Any], filename: str, directory: str
    ) -> str:
        for id, recipe_as_json in export_data.items():
            recipe_as_gz = os.path.join(directory, "recipe_" + id + ".paprikarecipe")
            with gzip.open(recipe_as_gz, "wb") as f:
                f.write(recipe_as_json.encode("utf-8"))
        filename_full_path = os.path.join(directory, filename)
        with zipfile.ZipFile(
            filename_full_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zip_file:
            gz_files = glob.glob(os.path.join(directory, "*.paprikarecipe"))
            print(gz_files)
            for gz_file in gz_files:
                zip_file.write(gz_file, arcname=os.path.basename(gz_file))
        return filename_full_path

    def get_cover_img_as_base64_string(
        self, recipe: Recipe
    ) -> tuple[str | None, str | None]:
        cover = self.get_cover(image_list=recipe.image_list)
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
                print(
                    f'Cover image for "{recipe.localized_title.de}" not found online any more.'
                )
            else:
                print(
                    f"While trying to fetch the cover img a HTTP error occurred: {exc.response.status_code}: {exc}"
                )
            return None, None
        return cover.name, base64.b64encode(response.content).decode("utf-8")

    def get_cover(self, image_list: list[Image]) -> Image | None:
        if not isinstance(image_list, list):
            raise ValueError("Parameter image_list must be a list")
        try:
            [cover] = [i for i in image_list if i.type == "cover"]
        except ValueError:
            return None
        return cover
