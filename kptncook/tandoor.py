"""
Export a single recipe to Tandoor

file format:
    1. export recipe to json
    2. compress file as zip: naming convention: <unique number>.zip
    3. zip this file as some_recipes.zip
"""

import json
import os
import re
import secrets
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import httpx
from jinja2 import Template
from unidecode import unidecode

from kptncook.config import settings
from kptncook.models import Image, Recipe

# language=jinja2
TANDOOR_RECIPE_TEMPLATE = """{
  "name": {{recipe.localized_title.de | tojson}},
  "description": {{recipe.author_comment.de | tojson}},
  "keywords": [{% if recipe.rtype %}
    {
      "name": {{ recipe.rtype | tojson }},
      "description": ""
    },{% endif %}
    {
      "name": "Kptncook",
      "description": ""
    }
  ],
  "working_time": {{recipe.preparation_time|default(0,true)}},
  "waiting_time": {{recipe.cooking_time|default(0,true)}},
  "servings": 3,
  "servings_text": "Portionen",
  "internal": true,
  "source_url": {{ ['https://mobile.kptncook.com/recipe/pinterest', (recipe.localized_title.de | urlencode), recipe.uid] | join('/') | tojson}},
  "nutrition": null,
  "steps": [{% set comma = joiner(",") %}{% for step in recipe.steps %}{{ comma() }}
    {
      "name": "",
      "instruction": {{step.title.de|default('unbekannter title',true) | tojson}},
      "time": 0,
      "order": {{loop.index - 1}},
      "show_ingredients_table": {% if not step.ingredients %}false,"ingredients": []{% else %}true,
      "ingredients": [{% set ingredientsComma = joiner(",") %}{% for stepIngredient in step.ingredients %}{{ ingredientsComma() }}
        {
          "food": {
            "name": {{stepIngredient.title.de|default('',true) | tojson}}
          },
          "unit": {% if stepIngredient.unit %}{
            "name": {{stepIngredient.unit.de.measure|default('Stück',true) | tojson}}
          }{% else %}null{% endif %},
          "amount": {% if stepIngredient.unit.de %}{{stepIngredient.unit.de.quantity * 3}}{% else %}0{% endif %},
          "order": {{loop.index - 1}},
          "is_header": false,
          "no_amount": {% if stepIngredient.unit %}false{% else %}true{% endif %}
        }{% endfor %}
      ]
      {% endif %}
    }{% endfor %}
  ]
}
"""  # noqa: E501


class GeneratedTandoorData:
    def __init__(
        self,
        cover_filename: str | None,
        cover_img: bytes | None,
        dtnow: str,
        hash_: str,
    ):
        self.cover_filename = cover_filename
        self.cover_img = cover_img
        self.dtnow = dtnow
        self.hash = hash_


class ExportData:
    def __init__(self, json: str | None, cover_img: str | None):
        self.json = json
        self.cover_img = cover_img


class TandoorExporter:
    invalid_control_chars = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F-\x9F]")
    template = Template(TANDOOR_RECIPE_TEMPLATE, trim_blocks=True)
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
            return self.asciify_string(s=recipes[0].localized_title.de) + ".zip"
        else:
            return "allrecipes.zip"

    def get_generated_data(self, recipe: Recipe) -> GeneratedTandoorData:
        """Just to make testing easier and only have one method to mock in tests."""
        cover_filename, cover_img = self.get_cover_img_as_bytes(recipe=recipe)
        dtnow = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        hash = secrets.token_hex(32)
        return GeneratedTandoorData(cover_filename, cover_img, dtnow, hash)

    def get_recipe_as_json_string(
        self, recipe: Recipe, generated: GeneratedTandoorData
    ) -> str:
        recipe_as_json = self.template.render(
            recipe=recipe,
            dtnow=generated.dtnow,
            cover_filename=generated.cover_filename,
            hash=generated.hash,
        )
        recipe_as_json = self.invalid_control_chars.sub("", recipe_as_json)
        recipe_as_json = self.unescaped_newline.sub(" ", recipe_as_json)
        json.loads(recipe_as_json)  # check if valid json
        return recipe_as_json

    def get_export_data(self, recipes: list[Recipe]) -> dict[str, ExportData]:
        export_data = dict()
        for recipe in recipes:
            try:
                generated = self.get_generated_data(recipe=recipe)
                recipe_as_json = self.get_recipe_as_json_string(
                    recipe=recipe, generated=generated
                )
                export_data[str(recipe.id.oid)] = ExportData(
                    recipe_as_json, generated.cover_img
                )
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
        self, export_data: dict[str, ExportData], filename: str, directory: str
    ) -> str:
        for id, recipe_and_image in export_data.items():
            recipe_as_json = recipe_and_image.json
            cover_img = recipe_and_image.cover_img
            recipe_as_zip = os.path.join(directory, "recipe_" + id + ".zip")
            with zipfile.ZipFile(
                recipe_as_zip, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as f:
                f.writestr("recipe.json", recipe_as_json.encode("utf-8"))
                f.writestr("image.jpg", cover_img)
        filename_full_path = os.path.join(directory, filename)
        with zipfile.ZipFile(
            filename_full_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zip_file:
            for id, recipe_as_json in export_data.items():
                recipe_as_zip = os.path.join(directory, "recipe_" + id + ".zip")
                zip_file.write(recipe_as_zip, arcname=os.path.basename(recipe_as_zip))
        return filename_full_path

    def get_cover_img_as_bytes(self, recipe: Recipe) -> tuple[str | None, bytes | None]:
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
        return cover.name, response.content

    def get_cover(self, image_list: list[Image]) -> Image | None:
        if not isinstance(image_list, list):
            raise ValueError("Parameter image_list must be a list")
        try:
            [cover] = [i for i in image_list if i.type == "cover"]
        except ValueError:
            return None
        return cover
