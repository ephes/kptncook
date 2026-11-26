"""Export recipes to a simple Markdown format.

Writes one `.md` file per recipe to an ``export_md`` directory under
``settings.root``. Section headings and image alt text are localized based on
``settings.kptncook_lang`` (falling back to English for unknown languages).
Recipe content (titles, steps, ingredient names) follows the project-wide
``localized_fallback`` convention shared with the other exporters.
"""

from __future__ import annotations

import json
import logging
import uuid
import tempfile
from contextlib import suppress

import httpx
from collections.abc import Iterable
from datetime import date
from itertools import islice
from pathlib import Path

from pathvalidate import sanitize_filename

from . import exporter_utils
from .config import get_settings
from .durability import fsync_directory
from .http_errors import UserFacingError
from .exporter_utils import (
    BatchExportError,
    ExportBudget,
    ExportPublicationUncertain,
    get_cover,
    format_quantity,
    ingredient_name,
    move_to_free_target,
    replace_timers_in_step,
    validate_export_id,
)
from .image_download import detect_image_extension, download_image
from .image_origin import ImageOriginError
from .ingredient_groups import iter_ingredient_groups
from .models import (
    DEFAULT_SERVINGS,
    Ingredient,
    LocalizedString,
    Recipe,
    RecipeStep,
    localized_fallback,
    normalize_language,
    scale_quantity,
)

logger = logging.getLogger(__name__)

# Localized structural labels. Recipe content language is governed by
# ``localized_fallback``; headings and text both prefer the configured language.
SECTION_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "ingredients": "Zutaten",
        "instructions": "Zubereitung",
        "notes": "Notizen / Empfehlungen",
        "image_alt": "Rezeptbild",
    },
    "en": {
        "ingredients": "Ingredients",
        "instructions": "Instructions",
        "notes": "Notes / Recommendations",
        "image_alt": "Recipe image",
    },
    "es": {
        "ingredients": "Ingredientes",
        "instructions": "Preparación",
        "notes": "Notas / Recomendaciones",
        "image_alt": "Imagen de la receta",
    },
    "fr": {
        "ingredients": "Ingrédients",
        "instructions": "Préparation",
        "notes": "Notes / Recommandations",
        "image_alt": "Image de la recette",
    },
    "pt": {
        "ingredients": "Ingredientes",
        "instructions": "Preparo",
        "notes": "Notas / Recomendações",
        "image_alt": "Imagem da receita",
    },
}

# The canned "mise en place" step KptnCook prepends to every recipe. Matched
# across languages so it is skipped regardless of the configured language.
PREP_STEP_TITLES = {
    "all set?",
    "alles parat?",
    "¿todo listo?",
    "vous avez tout ?",
    "tudo pronto?",
}


def _section_labels() -> dict[str, str]:
    lang = normalize_language(get_settings().kptncook_lang)
    return SECTION_LABELS.get(lang, SECTION_LABELS["en"])


def _is_prep_step(step: RecipeStep) -> bool:
    title: LocalizedString = step.title
    for value in (title.en, title.de, title.es, title.fr, title.pt):
        if value and value.replace("\xa0", " ").strip().lower() in PREP_STEP_TITLES:
            return True
    return False


def _yaml_string(value: str) -> str:
    # Keep astral characters intact; JSON's surrogate escapes are not valid YAML.
    # Escape raw Unicode line breaks/controls so YAML cannot fold or reject them.
    return "".join(
        char if char.isprintable() else f"\\U{ord(char):08x}"
        for char in json.dumps(value, ensure_ascii=False)
    )


class MarkdownExporter:
    def __init__(self, servings: int = DEFAULT_SERVINGS) -> None:
        # Markdown is final cookbook text that no other app rescales, so the
        # standard serving count is configurable here (``--servings``).
        if isinstance(servings, bool) or not isinstance(servings, int) or servings < 1:
            raise ValueError("servings must be a positive integer")
        self.servings = servings

    def servings_for(self, recipe: Recipe) -> int:
        # Batch recipes (cookies, cakes) are balanced for their fixed portion
        # count, which therefore wins over the requested servings.
        return recipe.servings_or(self.servings)

    def export(self, recipes: Iterable[Recipe]) -> list[Path]:
        export_date = date.today()
        recipes = list(islice(recipes, exporter_utils.MAX_RECIPES + 1))
        exporter_utils.validate_export_batch_size(len(recipes))
        for recipe in recipes:
            recipe.validate_scaled_quantities(self.servings_for(recipe))
            validate_export_id(recipe.id.oid)
        budget = ExportBudget()
        written: list[Path] = []
        written_assets: list[Path] = []

        out_dir = get_settings().root / "export_md"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            fsync_directory(out_dir.parent)
        except OSError as exc:
            raise UserFacingError(
                "Could not sync the Markdown export directory entry."
            ) from exc

        for index, recipe in enumerate(recipes):
            asset_path: Path | None = None
            try:
                title = (
                    localized_fallback(
                        recipe.localized_title, language=get_settings().kptncook_lang
                    )
                    or "recipe"
                )
                base = sanitize_filename(title)
                image = self._download_recipe_image(recipe, budget=budget)
                scratch = tempfile.TemporaryDirectory(dir=out_dir, prefix=".kptncook-")
                try:
                    staging = Path(scratch.name)
                    try:
                        if image is not None:
                            image_bytes, extension = image
                            suffix = f".{extension}"
                            staged_asset = staging / f"image{suffix}"
                            budget.charge_output(len(image_bytes))
                            with staged_asset.open("xb") as asset:
                                if asset.write(image_bytes) != len(image_bytes):
                                    raise UserFacingError(
                                        "Incomplete Markdown image staging write"
                                    )
                            assets = out_dir / "assets"
                            assets.mkdir(exist_ok=True)
                            try:
                                # Persist the assets directory's name before publishing
                                # its contents and then the referring note.
                                fsync_directory(out_dir)
                            except OSError as exc:
                                raise UserFacingError(
                                    "Could not sync the Markdown assets directory entry."
                                ) from exc
                            asset_path = move_to_free_target(
                                staged_asset,
                                assets,
                                uuid.uuid4().hex,
                                suffix,
                                budget=budget,
                            )
                        image_path = (
                            asset_path.relative_to(out_dir).as_posix()
                            if asset_path
                            else None
                        )
                        content = self.render_recipe(
                            recipe, image_path=image_path, export_date=export_date
                        )
                        staged_note = staging / "note.md"
                        note_bytes = content.encode("utf-8")
                        budget.charge_output(len(note_bytes))
                        if staged_note.write_bytes(note_bytes) != len(note_bytes):
                            raise UserFacingError(
                                "Incomplete Markdown note staging write"
                            )
                        out_path = move_to_free_target(
                            staged_note,
                            out_dir,
                            base,
                            ".md",
                            recipe.id.oid,
                            budget=budget,
                        )
                    except ExportPublicationUncertain:
                        # The asset or its referring note may be visible.
                        # Keep referenced assets while publication is uncertain.
                        raise
                    except BaseException:
                        if asset_path is not None:
                            try:
                                asset_path.unlink(missing_ok=True)
                                fsync_directory(asset_path.parent)
                            except OSError:
                                with suppress(Exception):
                                    logger.warning(
                                        "Markdown asset cleanup is uncertain for %s",
                                        asset_path,
                                    )
                        raise
                    written.append(out_path)
                    if asset_path is not None:
                        written_assets.append(asset_path)
                finally:
                    try:
                        scratch.cleanup()
                    except OSError:
                        with suppress(Exception):
                            logger.warning(
                                "Could not clean up Markdown staging directory."
                            )
            except Exception as exc:
                raise BatchExportError(
                    written,
                    recipe.id.oid,
                    len(recipes) - index - 1,
                    exc,
                    completed_assets=written_assets,
                    retained_assets=(asset_path,)
                    if asset_path is not None
                    and isinstance(exc, ExportPublicationUncertain)
                    else (),
                ) from exc
        return written

    def _download_recipe_image(
        self, recipe: Recipe, *, budget: ExportBudget | None = None
    ) -> tuple[bytes, str] | None:
        image = recipe.steps[-1].image if recipe.steps else None
        if image is None or not image.url:
            image = get_cover(recipe.image_list)
        if image is None:
            return None
        try:
            image = image.get_image_with_api_key_url(get_settings().kptncook_api_key)
        except ImageOriginError:
            logger.warning("Skipping Markdown image: not on an allowed image host")
            return None
        try:
            image_bytes = download_image(image.url, budget=budget)
        except httpx.HTTPError:
            logger.warning(
                "Skipping Markdown image: download failed or exceeded limits"
            )
            return None
        extension = detect_image_extension(image_bytes)
        if extension is None:
            logger.warning("Skipping Markdown image: unsupported image format")
            return None
        return image_bytes, extension

    def render_recipe(
        self,
        recipe: Recipe,
        image_path: str | None = None,
        *,
        export_date: date | None = None,
    ) -> str:
        recipe.validate_scaled_quantities(self.servings_for(recipe))
        labels = _section_labels()
        comment = (
            localized_fallback(
                recipe.author_comment, language=get_settings().kptncook_lang
            )
            or ""
        )

        lines: list[str] = ["---"]
        title = (
            localized_fallback(
                recipe.localized_title, language=get_settings().kptncook_lang
            )
            or "recipe"
        )
        lines.append(f"title: {_yaml_string(title)}")
        note_date = export_date if export_date is not None else date.today()
        lines.append(f"date: {note_date.isoformat()}")

        servings = self.servings_for(recipe)
        lines.append(f"servings: {servings}")

        lines.append(f"prepTime: {_yaml_string(f'{recipe.preparation_time}m')}")
        if recipe.cooking_time is not None:
            lines.append(f"cookTime: {_yaml_string(f'{recipe.cooking_time}m')}")
        lines.append('author: "KptnCook"')
        recipe_ref = recipe.uid or recipe.id.oid
        lines.append(
            f"link: {_yaml_string(f'https://mobile.kptncook.com/recipe/pinterest/{recipe_ref}')}"
        )

        if image_path:
            lines.append(f"image: {_yaml_string(image_path)}")

        active_tags = recipe.active_tags or []
        tags = ["vegan"] if recipe.rtype == "Vegan" else []
        tags.extend(self._transform_tags(active_tags))
        tags = list(dict.fromkeys(tags))
        lines.append("tags:" if tags else "tags: []")
        for tag in tags:
            lines.append(f"  - {_yaml_string(tag)}")

        if (
            "cooking_time_under_20" in active_tags
            or "under_five_ingredient" in active_tags
        ):
            lines.append("simple: true")

        lines.append("---")
        lines.append("")

        if comment:
            lines.append(comment)
            lines.append("")

        if image_path is not None:
            lines.append(f"![{labels['image_alt']}]({image_path})")
            lines.append("")

        # Ingredients
        lines.append(f"### {labels['ingredients']}")
        lines.append("")
        ing_lines = self.get_ingredients_lines(recipe.ingredients, servings)
        if ing_lines:
            lines.extend(ing_lines)
        else:
            lines.append("- ")
        lines.append("")

        # Instructions
        lines.append(f"### {labels['instructions']}")
        lines.append("")
        for step in recipe.steps:
            if _is_prep_step(step):
                continue
            step_text = (
                localized_fallback(step.title, language=get_settings().kptncook_lang)
                or ""
            )
            # normalize internal newlines
            step_text = step_text.replace("\n", " ")
            # replace <timer> placeholders with the step's timers (in minutes)
            step_text = replace_timers_in_step(step, step_text)
            lines.append(f"- {step_text}")
        lines.append("")

        # Notes / Recommendations
        lines.append(f"### {labels['notes']}")
        lines.append("")
        lines.append("- ")

        return "\n".join(lines)

    @staticmethod
    def _transform_tags(active_tags: list[str]) -> list[str]:
        # drop tags that are noise in a recipe note
        filtered_tags = [
            tag
            for tag in active_tags
            if not (
                tag.startswith("diet_")
                or tag.startswith("main_ingredient_")
                or "under_" in tag
                or "above_" in tag
                or "below_" in tag
                or "_friendly" in tag
            )
        ]

        # normalize a few tags into friendlier forms
        transformed: list[str] = []
        for tag in filtered_tags:
            if tag in {"spring", "summer", "fall", "winter"}:
                tag = f"season/{tag}"
            elif tag == "dessert_sweet":
                tag = "dessert"
            elif tag == "comfort_foot":  # upstream typo
                tag = "comfort_food"
            transformed.append(tag)
        return transformed

    def get_ingredients_lines(
        self, ingredients: list[Ingredient], servings: int = 1
    ) -> list[str]:
        lines: list[str] = []
        for group_label, group_ingredients in iter_ingredient_groups(ingredients):
            if group_label:
                lines.append(f"{group_label}:")
            for ingredient in group_ingredients:
                text = self.format_ingredient_line(ingredient, servings)
                if text:
                    lines.append(f"- {text}")
            lines.append("")
        return lines

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
