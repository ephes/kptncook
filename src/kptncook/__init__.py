"""
kptncook is a little command line utility to download
new recipes.
"""

import sys
from datetime import date
from typing import Optional

import httpx
import typer
from rich import print as rprint
from rich.pretty import pprint
from rich.prompt import Prompt

from .api import KptnCookClient, parse_id
from .config import settings
from .mealie import MealieApiClient, kptncook_to_mealie
from .models import Recipe
from .paprika import PaprikaExporter
from .repositories import RecipeRepository

__all__ = [
    "list_kptncook_today",
    "save_todays_recipes",
    "sync_with_mealie",
    "sync",
    "backup_kptncook_favorites",
    "get_kptncook_access_token",
    "list_recipes",
    "search_kptncook_recipe_by_id",
    "export_recipes_to_paprika",
]

__version__ = "0.0.23"
cli = typer.Typer()


@cli.command(name="kptncook-today")
def list_kptncook_today():
    """
    List all recipes for today from the kptncook site.
    """
    client = KptnCookClient()
    all_recipes = client.list_today()
    for recipe in all_recipes:
        pprint(recipe)


@cli.command(name="save-todays-recipes")
def save_todays_recipes():
    """
    Save recipes for today from kptncook site.
    """
    fs_repo = RecipeRepository(settings.root)
    if fs_repo.needs_to_be_synced(date.today()):
        client = KptnCookClient()
        fs_repo.add_list(client.list_today())


def get_mealie_client() -> MealieApiClient:
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


def get_kptncook_recipes_from_repository() -> list[Recipe]:
    fs_repo = RecipeRepository(settings.root)
    recipes = []
    for repo_recipe in fs_repo.list():
        # recipes.append(Recipe.model_validate(repo_recipe.data))
        try:
            recipes.append(Recipe.model_validate(repo_recipe.data))
        except Exception as e:
            print(f"Could not parse recipe {repo_recipe.id}: {e}")
            for ingredient in repo_recipe.data.get("ingredients"):
                uncountable_title = ingredient["ingredient"].get("uncountableTitle")
                if uncountable_title is None:
                    print("ingredient: ", ingredient["ingredient"])
            # return []
    return recipes


def get_recipe_from_repository_by_oid(oid: str) -> list[Recipe]:
    """
    get one single recipe from local repository
    :param oid: oid of recipe
    :return: list
    """
    recipes = get_kptncook_recipes_from_repository()
    return [recipe for num, recipe in enumerate(recipes) if recipe.id.oid == oid]


@cli.command(name="sync-with-mealie")
def sync_with_mealie():
    """
    Sync locally saved recipes with mealie.
    """
    try:
        client = get_mealie_client()
    except Exception as e:
        rprint(f"Could not login to mealie: {e}")
        sys.exit(1)
    kptncook_recipes_from_mealie = get_kptncook_recipes_from_mealie(client)
    recipes = get_kptncook_recipes_from_repository()
    kptncook_recipes_from_repository = [kptncook_to_mealie(r) for r in recipes]
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


@cli.command(name="backup-favorites")
def backup_kptncook_favorites():
    """
    Store kptncook favorites in local repository.
    """
    if settings.kptncook_access_token is None:
        rprint("Please set KPTNCOOK_ACCESS_TOKEN in your environment or .env file")
        sys.exit(1)
    client = KptnCookClient()
    favorites = client.list_favorites()
    rprint(f"Found {len(favorites)} favorites")
    ids = [("oid", oid["identifier"]) for oid in favorites]
    recipes = client.get_by_ids(ids)
    if len(recipes) == 0:
        rprint("Could not find any favorites")
        sys.exit(1)

    fs_repo = RecipeRepository(settings.root)
    fs_repo.add_list(recipes)
    rprint(f"Added {len(recipes)} recipes to local repository")


@cli.command(name="kptncook-access-token")
def get_kptncook_access_token():
    """
    Get access token for kptncook.
    """
    username = Prompt.ask("Enter your kptncook email address")
    password = Prompt.ask("Enter your kptncook password", password=True)
    client = KptnCookClient()
    access_token = client.get_access_token(username, password)
    rprint("your access token: ", access_token)


@cli.command(name="list-recipes")
def list_recipes():
    """
    List all locally saved recipes.
    """
    recipes = get_kptncook_recipes_from_repository()
    for num, recipe in enumerate(recipes):
        rprint(num, recipe.localized_title.de, recipe.id.oid)


@cli.command(name="search-by-id")
def search_kptncook_recipe_by_id(id_: str):
    """
    Search for a recipe by id in kptncook api, id can be a sharing
    url or an oid for example, and add it to the local repository.
    """
    if id_.startswith(
        "https://share.kptncook.com/"
    ):  # sharing url -> use redirect location
        r = httpx.get(id_)
        if r.status_code not in (301, 302):
            rprint("Could not get redirect location")
            sys.exit(1)
        id_ = r.headers["location"]
    parsed = parse_id(id_)
    if parsed is None:
        rprint("Could not parse id")
        sys.exit(1)
    id_type, id_value = parsed
    rprint(id_type, id_value)
    client = KptnCookClient()
    recipes = client.get_by_ids([(id_type, id_value)])
    if len(recipes) == 0:
        rprint("Could not find recipe")
        sys.exit(1)
    recipe = recipes[0]
    fs_repo = RecipeRepository(settings.root)
    fs_repo.add_list([recipe])
    rprint(f"Added recipe {id_type} {id_value} to local repository")


# Optional needed by typer, standalone to trick pyupgrade to not change it
OptionalId = Optional[str]


@cli.command(name="export-recipes-to-paprika")
def export_recipes_to_paprika(_id: OptionalId = typer.Argument(None)):
    """
    Export one recipe or all recipes to Paprika app

    Example usage 1:  kptncook  export-recipes-to-paprika 635a68635100007500061cd7
    Example usage 2:  kptncook  export-recipes-to-paprika
    """
    if _id:
        recipes = get_recipe_by_id(_id)
    else:
        recipes = get_kptncook_recipes_from_repository()
    exporter = PaprikaExporter()
    filename = exporter.export(recipes=recipes)
    rprint(
        "\n The data was exported to '%s'. Open the export file with the Paprika App.\n"
        % filename
    )


def get_recipe_by_id(_id: str):
    parsed = parse_id(_id)
    if parsed is None:
        rprint("Could not parse id")
        sys.exit(1)
    # we can expect always an oid here - correct?
    id_type, id_value = parsed
    found_recipes = get_recipe_from_repository_by_oid(oid=id_value)
    if len(found_recipes) == 0:
        rprint("Recipe not found.")
        sys.exit(1)
    if len(found_recipes) > 1:
        rprint("More than one recipe found with that ID.")
        sys.exit(1)
    return found_recipes


if __name__ == "__main__":
    cli()
