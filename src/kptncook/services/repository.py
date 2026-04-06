from __future__ import annotations

from datetime import date

from kptncook.config import get_settings
from kptncook.models import Recipe
from kptncook.repositories import RecipeInDb, RecipeRepository


def get_repository() -> RecipeRepository:
    return RecipeRepository(get_settings().root)


def repository_needs_sync(sync_date: date) -> bool:
    return get_repository().needs_to_be_synced(sync_date)


def save_recipe_entries(recipes: list[RecipeInDb]) -> int:
    if not recipes:
        return 0
    get_repository().add_list(recipes)
    return len(recipes)


def list_repository_entries() -> list[RecipeInDb]:
    return get_repository().list()


def list_repository_recipes() -> list[Recipe]:
    recipes: list[Recipe] = []
    for repo_recipe in list_repository_entries():
        try:
            recipes.append(Recipe.model_validate(repo_recipe.data))
        except Exception:
            continue
    return recipes


def get_repository_recipe_by_oid(oid: str) -> list[Recipe]:
    return [recipe for recipe in list_repository_recipes() if recipe.id.oid == oid]


def delete_recipe_ids(ids: list[str]) -> tuple[list[str], list[str]]:
    return get_repository().delete_by_ids(ids)


def list_repository_ids() -> dict[object, RecipeInDb]:
    return get_repository().list_by_id()
