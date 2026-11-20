"""Validate text and bound expanded recipe work before model hydration."""

import re
from collections.abc import Iterator, Sequence

from kptncook.repositories import MAX_STORE_BYTES

MAX_RECIPES = 4096
MAX_RECIPE_DEPTH = 32
# Per-recipe envelope: a real KptnCook recipe needs roughly 7,000 values/keys
# and 55,000 text characters, so these leave generous headroom for one recipe.
MAX_RECIPE_WORK = 262144
MAX_RECIPE_TEXT = 8 * 1024 * 1024
# Whole-batch value/key and text totals, sized to cover any store admitted
# under MAX_STORE_BYTES: each JSON value/key needs at least two bytes and each
# text character at least one byte. MAX_RECIPES still caps the recipe count.
MAX_BATCH_WORK = MAX_STORE_BYTES // 2
MAX_BATCH_TEXT = MAX_STORE_BYTES
_SURROGATE = re.compile(r"[\ud800-\udfff]")


class RecipeAdmissionError(ValueError):
    """A recipe batch contains invalid text or exceeds the model work envelope."""


def admit_recipes(recipes: Sequence[object]) -> None:
    if len(recipes) > MAX_RECIPES:
        raise RecipeAdmissionError("Recipe model work exceeds the recipe count limit")
    # Iterators keep wide containers incremental; active IDs detect cycles while
    # charging shared acyclic objects each time they would be hydrated.
    stack: list[tuple[Iterator[object], int, int | None]] = [(iter(recipes), 0, None)]
    active: set[int] = set()
    work = text = recipe_work = recipe_text = 0
    while stack:
        children, depth, owner = stack[-1]
        try:
            node = next(children)
        except StopIteration:
            stack.pop()
            if owner is not None:
                active.remove(owner)
            continue
        if depth == 0:
            # Each top-level item starts its own per-recipe envelope.
            recipe_work = recipe_text = 0
        work += 1
        recipe_work += 1
        if recipe_work > MAX_RECIPE_WORK or work > MAX_BATCH_WORK:
            raise RecipeAdmissionError("Recipe model work exceeds the value/key limit")
        if isinstance(node, str):
            text += len(node)
            recipe_text += len(node)
            if recipe_text > MAX_RECIPE_TEXT or text > MAX_BATCH_TEXT:
                raise RecipeAdmissionError("Recipe model work exceeds the text limit")
            if not node.isascii() and _SURROGATE.search(node):
                raise RecipeAdmissionError(
                    "Recipe contains invalid Unicode scalar text."
                )
        elif isinstance(node, (dict, list, tuple)):
            if depth >= MAX_RECIPE_DEPTH or id(node) in active:
                raise RecipeAdmissionError(
                    "Recipe model work is cyclic or exceeds the depth limit"
                )
            active.add(id(node))
            # Keys consume both work and text, including ignored metadata.
            children = (
                (part for pair in node.items() for part in pair)
                if isinstance(node, dict)
                else iter(node)
            )
            stack.append((children, depth + 1, id(node)))
