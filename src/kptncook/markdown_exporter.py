"""Export recipes to a simple Markdown format.

Writes one `.md` file per recipe to an ``export_md`` directory under
``settings.root``. Section headings and image alt text are localized based on
``settings.kptncook_lang`` (falling back to English for unknown languages).
Recipe content (titles, steps, ingredient names) follows the project-wide
``localized_fallback`` convention shared with the other exporters.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

from pathvalidate import sanitize_filename

from .config import get_settings
from .exporter_utils import get_cover, replace_timers_in_step
from .ingredient_groups import iter_ingredient_groups
from .models import Ingredient, LocalizedString, Recipe, RecipeStep, localized_fallback

# KptnCook quantities are already totals for the recipe's standard portions, so
# they are emitted as-is (matching the Paprika and Mealie exporters). This value
# is only used for the ``servings`` front matter when the recipe omits an
# explicit portion count.
DEFAULT_SERVINGS = 2

# Localized structural labels. Recipe content language is governed by
# ``localized_fallback``; only the headings/alt text below are language-aware.
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
    lang = (get_settings().kptncook_lang or "en").lower()
    return SECTION_LABELS.get(lang, SECTION_LABELS["en"])


def _is_prep_step(step: RecipeStep) -> bool:
    title: LocalizedString = step.title
    for value in (title.en, title.de, title.es, title.fr, title.pt):
        if value and value.replace("\xa0", " ").strip().lower() in PREP_STEP_TITLES:
            return True
    return False


class MarkdownExporter:
    def export(self, recipes: Iterable[Recipe]) -> list[Path]:
        written: list[Path] = []
        used_stems: set[str] = set()

        out_dir = get_settings().root / "export_md"
        out_dir.mkdir(parents=True, exist_ok=True)

        for recipe in recipes:
            title = localized_fallback(recipe.localized_title) or "recipe"
            base = sanitize_filename(title)
            # keep one file per recipe even when titles (or suffixed stems) collide
            stem = base
            counter = 1
            while stem in used_stems:
                suffix = recipe.id.oid if counter == 1 else f"{recipe.id.oid}-{counter}"
                stem = f"{base}-{suffix}"
                counter += 1
            used_stems.add(stem)
            out_path = out_dir / f"{stem}.md"
            out_path.write_text(self.render_recipe(recipe), encoding="utf-8")
            written.append(out_path)
        return written

    def render_recipe(self, recipe: Recipe) -> str:
        labels = _section_labels()
        comment = localized_fallback(recipe.author_comment) or ""

        lines: list[str] = ["---"]
        lines.append(f"date: {date.today().isoformat()}")

        servings = recipe.fixed_portion_count or DEFAULT_SERVINGS
        lines.append(f"servings: {servings}")

        lines.append(f"prepTime: {recipe.preparation_time}m")
        if recipe.cooking_time is not None:
            lines.append(f"cookTime: {recipe.cooking_time}m")
        lines.append("author: KptnCook")
        recipe_ref = recipe.uid or recipe.id.oid
        lines.append(f"link: https://mobile.kptncook.com/recipe/pinterest/{recipe_ref}")

        # cover image (prefer the last step image, else the cover image)
        last_step_image = recipe.steps[-1].image if recipe.steps else None
        image = last_step_image or get_cover(recipe.image_list)
        image_url = (
            f"{image.url}?kptnkey={get_settings().kptncook_api_key}" if image else None
        )

        if image_url:
            lines.append(f"image: {image_url}")

        lines.append("tags:")

        if recipe.rtype == "Vegan":
            lines.append("  - vegan")

        if recipe.active_tags:
            for tag in self._transform_tags(recipe.active_tags):
                lines.append(f"  - {tag}")

            if (
                "cooking_time_under_20" in recipe.active_tags
                or "under_five_ingredient" in recipe.active_tags
            ):
                lines.append("simple: true")

        lines.append("---")
        lines.append("")

        if comment:
            lines.append(comment)
            lines.append("")

        if image_url is not None:
            lines.append(f"![{labels['image_alt']}]({image_url})")
            lines.append("")

        # Ingredients
        lines.append(f"### {labels['ingredients']}")
        lines.append("")
        ing_lines = self.get_ingredients_lines(recipe.ingredients)
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
            step_text = localized_fallback(step.title) or ""
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

    def get_ingredients_lines(self, ingredients: list[Ingredient]) -> list[str]:
        lines: list[str] = []
        for group_label, group_ingredients in iter_ingredient_groups(ingredients):
            if group_label:
                lines.append(f"{group_label}:")
            for ingredient in group_ingredients:
                text = self.format_ingredient_line(ingredient)
                if text:
                    lines.append(f"- {text}")
            lines.append("")
        return lines

    def format_ingredient_line(self, ingredient: Ingredient) -> str:
        parts: list[str] = []
        if ingredient.quantity:
            parts.append("{0:g}".format(round(ingredient.quantity, 2)))
        if ingredient.measure:
            parts.append(ingredient.measure)
        ingredient_name = (
            localized_fallback(ingredient.ingredient.uncountable_title) or ""
        )
        if ingredient_name:
            parts.append(ingredient_name)
        return " ".join(part for part in parts if part).strip()
