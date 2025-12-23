from collections.abc import Iterable

from kptncook.config import settings
from kptncook.models import Ingredient

DEFAULT_INGREDIENT_GROUP_LABELS = {
    "regular": "You need",
    "basic": "Pantry",
}


def parse_ingredient_group_labels(raw: str | None) -> dict[str, str]:
    labels = dict(DEFAULT_INGREDIENT_GROUP_LABELS)
    if not raw:
        return labels
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        key, value = entry.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key or not value:
            continue
        labels[key] = value
    return labels


def _ingredient_group_key(ingredient: Ingredient) -> str:
    details = getattr(ingredient, "ingredient", None)
    key = getattr(details, "typ", None)
    if isinstance(key, str) and key.strip():
        return key.strip().lower()
    return "other"


def _format_group_label(key: str, label_map: dict[str, str]) -> str:
    if key in label_map:
        return label_map[key]
    return key.replace("_", " ").title()


def iter_ingredient_groups(
    ingredients: Iterable[Ingredient],
) -> list[tuple[str | None, list[Ingredient]]]:
    items = list(ingredients)
    if not settings.kptncook_group_ingredients_by_typ:
        return [(None, items)]
    label_map = parse_ingredient_group_labels(settings.kptncook_ingredient_group_labels)
    groups: dict[str, list[Ingredient]] = {}
    seen_order: list[str] = []
    for ingredient in items:
        key = _ingredient_group_key(ingredient)
        if key not in groups:
            groups[key] = []
            seen_order.append(key)
        groups[key].append(ingredient)
    ordered_keys = [key for key in label_map if key in groups]
    for key in seen_order:
        if key not in ordered_keys:
            ordered_keys.append(key)
    return [(_format_group_label(key, label_map), groups[key]) for key in ordered_keys]
