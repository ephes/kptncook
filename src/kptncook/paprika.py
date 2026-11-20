"""
Export a single recipe to Paprika App

file format:
    1. export recipe to json
    2. compress file as gz: naming convention: a_recipe_name.paprikarecipe (Singular)
    3. zip this file as some_recipes.paprikarecipes (Plural!)
"""

import base64
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
from kptncook.durability import fsync_directory
from kptncook.exporter_utils import (
    BatchExportError,
    BudgetedWriter,
    ExportBudget,
    ExportLimitError,
    ExportPublicationUncertain,
    validate_export_batch_size,
    asciify_string,
    bounded_export_filename,
    get_cover,
    format_quantity,
    ingredient_name,
    get_step_text,
    move_to_free_target,
    write_zip,
    validate_export_id,
)
from kptncook.image_download import download_image
from kptncook.ingredient_groups import iter_ingredient_groups
from kptncook.models import (
    Image,
    Ingredient,
    Recipe,
    localized_fallback,
    scale_quantity,
)

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
    def export(self, recipes: list[Recipe]) -> str:
        validate_export_batch_size(len(recipes))
        for recipe in recipes:
            recipe.validate_scaled_quantities()
            validate_export_id(recipe.id.oid)
        # Retain only recipe references, with the same last-identity-wins policy
        # as get_export_data; serialized covers/JSON live for one recipe only.
        unique = {recipe.id.oid: recipe for recipe in recipes}
        filename = self.get_export_filename(export_data=unique, recipes=recipes)
        budget = ExportBudget()
        index = -1
        failed_recipe_id = "archive"
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                entries = []
                for index, (identifier, recipe) in enumerate(unique.items()):
                    failed_recipe_id = identifier
                    path = Path(tmp_dir) / f"recipe_{identifier}.paprikarecipe"
                    data = self.get_recipe_as_json_string(recipe, budget=budget).encode(
                        "utf-8"
                    )
                    budget.charge_output(len(data))
                    with path.open("wb") as output:
                        with gzip.GzipFile(
                            filename=str(path),
                            mode="wb",
                            fileobj=BudgetedWriter(output, budget),
                        ) as compressed:
                            compressed.write(data)
                    entries.append((path.name, path))
                filename_full_path = Path(tmp_dir) / filename
                failed_recipe_id = "archive"
                write_zip(filename_full_path, entries, budget=budget)
                # never overwrite an existing export; pick a free name instead
                disambiguator = recipes[0].id.oid if len(unique) == 1 else None
                target = move_to_free_target(
                    source=filename_full_path,
                    directory=Path.cwd(),
                    stem=Path(filename).stem,
                    extension=Path(filename).suffix,
                    disambiguator=disambiguator,
                    budget=budget,
                )
                return target.name
        except (ExportLimitError, ExportPublicationUncertain) as exc:
            # Paprika publishes one archive: staged recipes are not receipts.
            raise BatchExportError(
                (),
                failed_recipe_id,
                len(unique) - index - 1,
                exc,
                unpublished_count=len(unique),
            ) from exc

    def get_export_filename(
        self, export_data: dict[str, Any], recipes: list[Recipe]
    ) -> str:
        if len(export_data) == 1:
            return bounded_export_filename(
                asciify_string(
                    s=localized_fallback(
                        recipes[0].localized_title,
                        language=get_settings().kptncook_lang,
                    )
                    or "recipe"
                ),
                ".paprikarecipes",
            )
        else:
            return "allrecipes.paprikarecipes"

    def get_generated_data(
        self, recipe: Recipe, *, budget: ExportBudget | None = None
    ) -> GeneratedData:
        """Just to make testing easier and only have one method to mock in tests."""
        cover_filename, cover_img = self.get_cover_img_as_base64_string(
            recipe=recipe, budget=budget
        )
        dtnow = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        hash = secrets.token_hex(32)
        return GeneratedData(cover_filename, cover_img, dtnow, hash)

    def get_recipe_as_json_string(
        self, recipe: Recipe, *, budget: ExportBudget | None = None
    ) -> str:
        recipe.validate_scaled_quantities()
        generated = (
            self.get_generated_data(recipe=recipe, budget=budget)
            if budget
            else self.get_generated_data(recipe=recipe)
        )
        document = {
            "uid": str(recipe.id.oid),
            "name": localized_fallback(
                recipe.localized_title, language=get_settings().kptncook_lang
            )
            or "",
            "directions": "".join(
                (get_step_text(step, language=get_settings().kptncook_lang) or "")
                + "\n"
                for step in recipe.steps
            ),
            "servings": str(recipe.servings),
            "rating": 0,
            "difficulty": "",
            "ingredients": self.get_ingredients_text(
                recipe.ingredients, servings=recipe.servings
            ),
            "notes": "",
            "created": generated.dtnow,
            "image_url": None,
            "cook_time": ""
            if recipe.cooking_time is None
            else str(recipe.cooking_time),
            "prep_time": str(recipe.preparation_time),
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

    def get_ingredients_text(
        self, ingredients: list[Ingredient], servings: int = 1
    ) -> str:
        lines = []
        for group_label, group_ingredients in iter_ingredient_groups(ingredients):
            if group_label:
                lines.append(f"{group_label}:")
            for ingredient in group_ingredients:
                line = self.format_ingredient_line(ingredient, servings)
                if line:
                    lines.append(line)
        if not lines:
            return ""
        return "\n".join(lines) + "\n"

    def format_ingredient_line(self, ingredient: Ingredient, servings: int = 1) -> str:
        parts: list[str] = []
        quantity = scale_quantity(ingredient.quantity, servings)
        if quantity:
            parts.append(format_quantity(quantity))
        if ingredient.measure:
            parts.append(ingredient.measure)
        name = ingredient_name(ingredient.ingredient, get_settings().kptncook_lang)
        if name:
            parts.append(name)
        return " ".join(part for part in parts if part).strip()

    def get_export_data(self, recipes: list[Recipe]) -> dict[str, str]:
        for recipe in recipes:
            recipe.validate_scaled_quantities()
            validate_export_id(recipe.id.oid)
        return {
            str(recipe.id.oid): self.get_recipe_as_json_string(recipe)
            for recipe in recipes
        }

    def save_recipes(
        self, export_data: dict[str, Any], filename: str, directory: str
    ) -> str:
        for identifier in export_data:
            validate_export_id(identifier)

        filename_full_path = os.path.join(directory, filename)
        archive_target = Path(filename_full_path)
        sidecar_directory = Path(directory)
        # Each staging directory shares a filesystem with its final targets.
        with (
            tempfile.TemporaryDirectory(dir=sidecar_directory) as member_dir,
            tempfile.TemporaryDirectory(dir=archive_target.parent) as archive_dir,
        ):
            entries = []
            for index, (identifier, recipe_as_json) in enumerate(export_data.items()):
                name = f"recipe_{identifier}.paprikarecipe"
                member = Path(member_dir) / str(index)
                with member.open("wb") as staged:
                    with gzip.GzipFile(
                        filename=name, mode="wb", fileobj=staged
                    ) as compressed:
                        compressed.write(recipe_as_json.encode("utf-8"))
                    staged.flush()
                    os.fsync(staged.fileno())
                entries.append((name, member))

            archive = Path(archive_dir) / "archive.zip"
            write_zip(archive, entries)
            with archive.open("rb") as staged:
                os.fsync(staged.fileno())

            for name, member in entries:
                member.replace(sidecar_directory / name)
            archive.replace(archive_target)
            try:
                fsync_directory(archive_target.parent)
                if entries and sidecar_directory != archive_target.parent:
                    fsync_directory(sidecar_directory)
            except BaseException as exc:
                raise ExportPublicationUncertain(archive_target) from exc
        return filename_full_path

    def get_cover_img_as_base64_string(
        self, recipe: Recipe, *, budget: ExportBudget | None = None
    ) -> tuple[str | None, str | None]:
        cover = get_cover(image_list=recipe.image_list)
        if cover is None:
            return None, None
        cover_url = recipe.get_image_url(api_key=get_settings().kptncook_api_key)
        if not isinstance(cover_url, str):
            return None, None
        try:
            image_bytes = download_image(cover_url, budget=budget)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                title = (
                    localized_fallback(
                        recipe.localized_title, language=get_settings().kptncook_lang
                    )
                    or "kptncook-recipe"
                )
                logger.warning('Cover image for "%s" not found online any more.', title)
            else:
                logger.error(
                    "HTTP error while fetching cover image (%s)",
                    exc.response.status_code,
                )
            return None, None
        except httpx.RequestError:
            logger.warning("Skipping cover image: download failed or exceeded limits")
            return None, None
        return cover.name, base64.b64encode(image_bytes).decode("utf-8")

    def asciify_string(self, s: str) -> str:
        return asciify_string(s)

    def get_cover(self, image_list: list[Image] | None) -> Image | None:
        if not isinstance(image_list, list):
            raise ValueError("Parameter image_list must be a list")
        return get_cover(image_list)
