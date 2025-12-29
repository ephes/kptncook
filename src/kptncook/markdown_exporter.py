"""Export recipes to a simple Markdown format.

Writes one `.md` file per recipe to `settings.root`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .config import settings
from .ingredient_groups import iter_ingredient_groups
from .models import Ingredient, Recipe, localized_fallback
from .exporter_utils import get_cover, replace_timers_in_step
from pathvalidate import sanitize_filename

SERVINGS_FACTOR = 4

class MarkdownExporter:
    def export(self, recipes: Iterable[Recipe]) -> List[Path]:
        written: List[Path] = []
        for recipe in recipes:
            title = localized_fallback(recipe.localized_title) or "recipe"
            filename = sanitize_filename(title) + ".md"
            out_path = Path(settings.root) / filename
            contents = self.render_recipe(recipe)
            out_path.write_text(contents, encoding="utf-8")
            written.append(out_path)
        return written

    def render_recipe(self, recipe: Recipe) -> str:
        from datetime import date as _date

        title = localized_fallback(recipe.localized_title) or "recipe"
        comment = localized_fallback(recipe.author_comment) or ""

        fm_lines: list[str] = ["---"]
        fm_lines.append(f"date: {_date.today().isoformat()}")
        fm_lines.append(f"yield: {SERVINGS_FACTOR}")

        fm_lines.append(f"prepTime: {recipe.preparation_time}m")
        fm_lines.append(f"cookTime: {recipe.cooking_time}m")
        fm_lines.append("author: KptnCook")
        fm_lines.append(
            f"url: https://mobile.kptncook.com/recipe/pinterest/{recipe.uid}"
        )

        fm_lines.append("tags:")
        if recipe.active_tags:
            for tag in recipe.active_tags:
                if not (
                    tag.startswith("diet_")
                    or tag.startswith("budget_")
                    or tag.startswith("main_ingredient_")
                    or tag.startswith("calories_")
                ):
                    fm_lines.append(f"- {tag}")

        fm_lines.append("---")
        fm_lines.append("")

        lines: list[str] = fm_lines

        # Title and optional comment
        lines.append(f"# {title}")
        lines.append("")
        if comment:
            lines.append(comment)
            lines.append("")

        # cover image (use last step image, else cover image)
        image = recipe.steps[-1].image or get_cover(recipe.image_list)
        if image is not None:
            image_url = f"{image.url}?kptnkey={settings.kptncook_api_key}"
            if isinstance(image_url, str) and image_url:
                lines.append(f"![Rezeptbild]({image_url})")
                lines.append("")

        # Ingredients
        lines.append("### Zutaten")
        lines.append("")
        ing_lines = self.get_ingredients_lines(recipe.ingredients)
        if ing_lines:
            lines.extend(ing_lines)
        else:
            lines.append("- ")
        lines.append("")

        # Instructions
        lines.append("### Zubereitung")
        lines.append("")
        for step in recipe.steps:
            step_text = localized_fallback(step.title) or ""
            if step_text == "Alles parat?":
                continue
            # normalize internal newlines
            step_text = step_text.replace("\n", " ")
            # replace <timer> placeholders with timers from the step (use minOrExact)
            step_text = replace_timers_in_step(step, step_text)
            lines.append(f"- {step_text}")
        lines.append("")

        # Notizen / Empfehlungen
        lines.append("### Notizen / Empfehlungen")
        lines.append("")
        
        return "\n".join(lines)

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
            parts.append("{0:g}".format(ingredient.quantity * SERVINGS_FACTOR))
        if ingredient.measure:
            parts.append(ingredient.measure)
        ingredient_name = (
            localized_fallback(ingredient.ingredient.uncountable_title) or ""
        )
        if ingredient_name:
            parts.append(ingredient_name)
        return " ".join(part for part in parts if part).strip()
