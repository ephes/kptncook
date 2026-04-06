from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError

from kptncook.config import get_settings
from kptncook.models import Recipe
from kptncook.repositories import (
    RecipeInDb,
    RecipeRepository,
    RepositoryError,
    format_validation_error,
)

logger = logging.getLogger(__name__)


class RepositoryServiceError(Exception):
    """Raised when repository access fails at the service boundary."""


@dataclass(frozen=True)
class InvalidStoredRecipe:
    position: int
    recipe_id: str | None
    reason: str


@dataclass(frozen=True)
class RepositoryRecipesResult:
    recipes: list[Recipe]
    invalid_entries: list[InvalidStoredRecipe]


def _extract_recipe_id(data: dict[object, object]) -> str | None:
    raw_id = data.get("_id")
    if isinstance(raw_id, dict):
        oid = raw_id.get("$oid")
        if isinstance(oid, str):
            return oid
    return None


def _log_invalid_entries(invalid_entries: list[InvalidStoredRecipe]) -> None:
    for entry in invalid_entries:
        label = entry.recipe_id or f"entry #{entry.position}"
        logger.warning("Skipping invalid stored recipe %s: %s", label, entry.reason)


def get_repository() -> RecipeRepository:
    return RecipeRepository(get_settings().root)


def repository_needs_sync(sync_date: date) -> bool:
    try:
        return get_repository().needs_to_be_synced(sync_date)
    except RepositoryError as exc:
        raise RepositoryServiceError(str(exc)) from exc


def save_recipe_entries(recipes: list[RecipeInDb]) -> int:
    if not recipes:
        return 0
    try:
        get_repository().add_list(recipes)
    except RepositoryError as exc:
        raise RepositoryServiceError(str(exc)) from exc
    return len(recipes)


def list_repository_entries() -> list[RecipeInDb]:
    try:
        return get_repository().list()
    except RepositoryError as exc:
        raise RepositoryServiceError(str(exc)) from exc


def load_repository_recipes() -> RepositoryRecipesResult:
    recipes: list[Recipe] = []
    invalid_entries: list[InvalidStoredRecipe] = []
    for position, repo_recipe in enumerate(list_repository_entries(), start=1):
        try:
            recipes.append(Recipe.model_validate(repo_recipe.data))
        except ValidationError as exc:
            invalid_entries.append(
                InvalidStoredRecipe(
                    position=position,
                    recipe_id=_extract_recipe_id(repo_recipe.data),
                    reason=format_validation_error(exc),
                )
            )
    return RepositoryRecipesResult(recipes=recipes, invalid_entries=invalid_entries)


def list_repository_recipes() -> list[Recipe]:
    result = load_repository_recipes()
    _log_invalid_entries(result.invalid_entries)
    return result.recipes


def get_repository_recipe_by_oid(oid: str) -> list[Recipe]:
    result = load_repository_recipes()
    _log_invalid_entries(result.invalid_entries)
    return [recipe for recipe in result.recipes if recipe.id.oid == oid]


def delete_recipe_ids(ids: list[str]) -> tuple[list[str], list[str]]:
    try:
        return get_repository().delete_by_ids(ids)
    except RepositoryError as exc:
        raise RepositoryServiceError(str(exc)) from exc


def list_repository_ids() -> dict[object, RecipeInDb]:
    try:
        return get_repository().list_by_id()
    except RepositoryError as exc:
        raise RepositoryServiceError(str(exc)) from exc
