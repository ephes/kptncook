"""
Export a single recipe to Paprika App

(file format:
    1. export recipe to json
    2. compress file as gz: naming convention: a_recipe_name.paprikarecipe (Singular)
    3. zip this file as some_recipes.paprikarecipes (Plural!)
"""
import os
import re
import shutil
import uuid
import requests
import base64
import zipfile
import gzip
import secrets
import tempfile
from pathlib import Path
from datetime import datetime

from jinja2 import Environment, FileSystemLoader
from unidecode import unidecode

from kptncook.models import Recipe
from kptncook.config import settings


class PaprikaExporter():

    def export(self, recipe: Recipe):
        renderer = ExportRenderer()
        cover_filename, cover_img = self.get_cover_img_as_base64_string(recipe=recipe)

        recipe_as_json = renderer.render(template_name="paprika.jinja2.json",
                                         recipe=recipe,
                                         uid=str(uuid.uuid4()).upper(),
                                         dtnow=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                         cover_filename=cover_filename,
                                         hash=secrets.token_hex(32),
                                         cover_img=cover_img)
        filename = self.asciify_string(s=recipe.localized_title.de) + ".paprikarecipes"
        tmp_dir = tempfile.mkdtemp()
        filename_full_path = self.save_recipe(recipe_as_json=recipe_as_json,
                                              filename=filename, dir=tmp_dir)
        self.move_to_target_dir(source=filename_full_path, target=os.path.join(str(Path.cwd()), filename))
        return filename

    def move_to_target_dir(self, source: str, target: str):
        shutil.move(source, target)

    def asciify_string(self, s):
        s = unidecode(s)
        s = re.sub(r'[^\w\s]', '_', s)
        s = re.sub(r'\s+', '_', s)
        return s

    def save_recipe(self,
                    recipe_as_json: str,
                    filename: str,
                    dir: str):
        recipe_as_gz = os.path.join(dir, "arecipe.paprikarecipe")
        with gzip.open(recipe_as_gz, "wb") as f:
            f.write(recipe_as_json.encode("utf-8"))
        filename_full_path = os.path.join(dir, filename)
        with zipfile.ZipFile(filename_full_path, "w",
                             compression=zipfile.ZIP_DEFLATED,
                             allowZip64=True) as zip_file:
            zip_file.write(recipe_as_gz)
        return filename_full_path

    def get_cover_img_as_base64_string(self, recipe: Recipe):
        cover = self.get_cover(image_list=recipe.image_list)
        cover_url = recipe.get_image_url(api_key=settings.kptncook_api_key)
        if not isinstance(cover_url, str):
            return None

        response = requests.get(cover_url)
        if response.status_code == 200:
            return cover.name, base64.b64encode(response.content).decode("utf-8")

    def get_cover(self, image_list: list):
        if not isinstance(image_list, list):
            raise ValueError("Parameter image_list must be a list")
        try:
            [cover] = [i for i in image_list if i.type == "cover"]
        except ValueError:
            return None
        return cover


class ExportRenderer():
    def render(self, template_name: str, recipe: Recipe, **kwargs) -> str:
        environment = self.get_environement()
        template = environment.get_template(template_name)
        return template.render(recipe=recipe, **kwargs)

    def get_template_dir(self):
        module_path = os.path.abspath(__file__)
        real_path = os.path.realpath(module_path)
        root_dir = os.path.dirname(os.path.dirname(real_path))
        return os.path.join(root_dir, "templates")

    def get_environement(self):
        return Environment(loader=FileSystemLoader(self.get_template_dir()), trim_blocks=True)
