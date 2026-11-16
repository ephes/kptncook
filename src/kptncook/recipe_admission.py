"""Bound expanded source/restored recipe work before model hydration."""

from collections.abc import Iterator, Sequence

MAX_RECIPES = 4096
MAX_RECIPE_DEPTH = 32
MAX_RECIPE_WORK = 262144
MAX_RECIPE_TEXT = 8 * 1024 * 1024


class RecipeAdmissionError(ValueError):
    """A whole recipe batch exceeds the supported model work envelope."""


def admit_recipes(recipes: Sequence[object]) -> None:
    if len(recipes) > MAX_RECIPES:
        raise RecipeAdmissionError("Recipe model work exceeds the recipe count limit")
    # Iterators keep wide containers incremental; active IDs detect cycles while
    # charging shared acyclic objects each time they would be hydrated.
    stack: list[tuple[Iterator[object], int, int | None]] = [(iter(recipes), 0, None)]
    active: set[int] = set()
    work = text = 0
    while stack:
        children, depth, owner = stack[-1]
        try:
            node = next(children)
        except StopIteration:
            stack.pop()
            if owner is not None:
                active.remove(owner)
            continue
        work += 1
        if work > MAX_RECIPE_WORK:
            raise RecipeAdmissionError("Recipe model work exceeds the value/key limit")
        if isinstance(node, str):
            text += len(node)
            if text > MAX_RECIPE_TEXT:
                raise RecipeAdmissionError("Recipe model work exceeds the text limit")
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
