"""
kptncook is a little command line utility to download
new recipes.
"""

from datetime import date

import typer
from rich.pretty import pprint

from .config import settings
from .repositories import HttpRepository, RecipeRepository

__all__ = ["list_all_recipes"]

__version__ = "0.0.01"
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


@cli.command(name="sync")
def sync():
    """
    Sync recipes for today from kptncook site.
    """
    fs_repo = RecipeRepository(settings.root)
    if fs_repo.needs_to_be_synced(date.today()):
        http_repo = HttpRepository()
        fs_repo.add_list(http_repo.list_today())


if __name__ == "__main__":
    cli()
