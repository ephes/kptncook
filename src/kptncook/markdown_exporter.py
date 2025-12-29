"""Export recipes to a simple Markdown format.

Writes one `.md` file per recipe to `settings.root`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .config import settings
from .ingredient_groups import iter_ingredient_groups
from .models import Ingredient, Recipe, localized_fallback
from .exporter_utils import get_cover
from pathvalidate import sanitize_filename


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

        # frontmatter
        fm_lines: list[str] = ["---"]
        fm_lines.append(f"date: {_date.today().isoformat()}")

        # yield (servings) - not available on model, leave empty
        fm_lines.append("yield: ")

        # cookTime and totalTime
        cook = f"{recipe.cooking_time}m" if recipe.cooking_time else ""
        prep = f"{recipe.preparation_time}m" if recipe.preparation_time else ""
        if cook and prep:
            try:
                # try to compute totals in minutes (assumes ints)
                total_minutes = (recipe.cooking_time or 0) + (recipe.preparation_time or 0)
                total = f"{total_minutes}m"
            except Exception:
                total = ""
        else:
            total = ""
        fm_lines.append(f"cookTime: {cook}")
        fm_lines.append(f"totalTime: {total}")

        # author, url, video - not present on model
        fm_lines.append("author: ")
        fm_lines.append("url: ")
        fm_lines.append("video: ")

        # cuisine - leave empty list
        fm_lines.append("cuisine:")

        # category - use active_tags if available
        fm_lines.append("category:")
        if recipe.active_tags:
            for tag in recipe.active_tags:
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

        # cover image (use cover url if available)
        cover = get_cover(recipe.image_list)
        if cover is not None:
            cover_url = recipe.get_image_url(api_key=settings.kptncook_api_key)
            if isinstance(cover_url, str) and cover_url:
                lines.append(f"![Image]({cover_url})")
                lines.append("")

        # Ingredients
        lines.append("### Ingredients")
        lines.append("")
        ing_lines = self.get_ingredients_lines(recipe.ingredients)
        if ing_lines:
            lines.extend([f"- {l}" for l in ing_lines])
        else:
            lines.append("- ")
        lines.append("")

        # Instructions
        lines.append("### Instructions")
        lines.append("")
        for step in recipe.steps:
            step_text = localized_fallback(step.title) or ""
            # normalize internal newlines
            step_text = step_text.replace("\n", " ")
            lines.append(f"- {step_text}")
        lines.append("")

        # Notizen / Empfehlungen
        lines.append("### Notizen / Empfehlungen")
        lines.append("")
        if comment:
            lines.append(comment)
        else:
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
                    lines.append(text)
        return lines

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
