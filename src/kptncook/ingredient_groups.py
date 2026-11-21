from collections.abc import Iterable

from pydantic_core import PydanticCustomError

from kptncook.config import get_settings
from kptncook.models import Ingredient

DEFAULT_INGREDIENT_GROUP_LABELS = {
    "regular": "You need",
    "basic": "Pantry",
}


def parse_ingredient_group_labels(raw: str | None) -> dict[str, str]:
    configured: dict[str, str] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        key, value = entry.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key or not value:
            continue
        if key in configured and configured[key] != value:
            raise PydanticCustomError(
                "ingredient_group_label_conflict",
                "Conflicting labels for the same ingredient group",
            )
        configured[key] = value
    return DEFAULT_INGREDIENT_GROUP_LABELS | configured


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
    settings = get_settings()
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
    ordered_key_set = set(ordered_keys)
    for key in seen_order:
        if key not in ordered_key_set:
            ordered_keys.append(key)
            ordered_key_set.add(key)
    return [(_format_group_label(key, label_map), groups[key]) for key in ordered_keys]
