"""
kptncook is a little command line utility to download
new recipes.
"""

from datetime import date

import httpx
import typer
from rich import print as rprint
from rich.pretty import pprint

from .config import settings
from .mealie import MealieApiClient, kptncook_to_mealie
from .models import Recipe
from .repositories import HttpRepository, RecipeRepository

__all__ = ["list_http"]

__version__ = "0.0.4"
cli = typer.Typer()


@cli.command(name="http")
def list_http():
    """
    List all recipes for today the kptncook site.
    """
    repo = HttpRepository()
    all_recipes = repo.list_today()
    for recipe in all_recipes:
        pprint(recipe)


@cli.command(name="save_todays_recipes")
def save_todays_recipes():
    """
    Save recipes for today from kptncook site.
    """
    fs_repo = RecipeRepository(settings.root)
    if fs_repo.needs_to_be_synced(date.today()):
        http_repo = HttpRepository()
        fs_repo.add_list(http_repo.list_today())


def get_client() -> MealieApiClient:
    client = MealieApiClient(settings.mealie_url)
    client.login(settings.mealie_username, settings.mealie_password)
    return client


def get_kptncook_recipes_from_mealie(client):
    recipes = client.get_all_recipes()
    recipes_with_details = []
    for recipe in recipes:
        recipes_with_details.append(client.get_via_slug(recipe.slug))
    kptncook_recipes = [
        r for r in recipes_with_details if r.extras.get("source") == "kptncook"
    ]
    return kptncook_recipes


def get_kptncook_recipes_from_repository():
    fs_repo = RecipeRepository(settings.root)
    api_recipes = []
    for repo_recipe in fs_repo.list():
        recipe = Recipe.parse_obj(repo_recipe.data)
        mealie_recipe = kptncook_to_mealie(recipe)
        api_recipes.append(mealie_recipe)
    return api_recipes


@cli.command(name="sync_with_mealie")
def sync_with_mealie():
    """
    Sync recipes from KptnCook with mealie.
    """
    client = get_client()
    kptncook_recipes_from_mealie = get_kptncook_recipes_from_mealie(client)
    kptncook_recipes_from_repository = get_kptncook_recipes_from_repository()
    ids_in_mealie = {r.extras["kptncook_id"] for r in kptncook_recipes_from_mealie}
    ids_from_api = {r.extras["kptncook_id"] for r in kptncook_recipes_from_repository}
    ids_to_add = ids_from_api - ids_in_mealie
    recipes_to_add = []
    for recipe in kptncook_recipes_from_repository:
        if recipe.extras.get("kptncook_id") in ids_to_add:
            recipes_to_add.append(recipe)
    created_slugs = []
    for recipe in recipes_to_add:
        try:
            created = client.create_recipe(recipe)
            created_slugs.append(created.slug)
        except httpx.HTTPStatusError as e:
            if (
                e.response.json().get("detail", {}).get("message")
                == "Recipe already exists"
            ):
                continue
    rprint(f"Created {len(created_slugs)} recipes")


@cli.command(name="sync")
def sync():
    """
    Fetch recipes for today from api, save them to disk and sync with mealie
    afterwards.
    """
    save_todays_recipes()
    sync_with_mealie()


if __name__ == "__main__":
    cli()
