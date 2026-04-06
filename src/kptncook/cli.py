from __future__ import annotations

import sys
from collections.abc import Callable
from typing import NoReturn, Optional, ParamSpec, TypeVar

import click
import typer
from rich import print as rprint
from rich.pretty import pprint
from typer.main import get_command

from kptncook.config import SettingsError, render_settings_error
from kptncook.models import localized_fallback
from kptncook.services.repository import InvalidStoredRecipe
from kptncook.services.discovery import (
    DISCOVERY_LIST_TYPES_REQUIRE_ID,
    _extract_ingredient_id,
    _extract_ingredient_name,
    normalize_discovery_list_id,
    normalize_discovery_list_type,
    normalize_ingredient_ids,
    normalize_tags,
)
from kptncook.services.repository import save_recipe_entries
from kptncook.services.workflows import (
    UserFacingError,
    backup_kptncook_favorites as backup_kptncook_favorites_workflow,
    delete_recipes_by_selection,
    delete_repository_recipes,
    export_recipes_to_paprika_result as export_recipes_to_paprika_workflow,
    export_recipes_to_tandoor_result as export_recipes_to_tandoor_workflow,
    get_discovery_list_recipes,
    get_discovery_screen,
    get_kptncook_access_token as get_kptncook_access_token_workflow,
    get_onboarding_recipes,
    get_recipes_with_ingredients,
    get_today_recipes,
    load_kptncook_recipes_from_repository,
    list_dailies as list_dailies_workflow,
    list_popular_ingredients as list_popular_ingredients_workflow,
    save_todays_recipes as save_todays_recipes_workflow,
    search_recipe_by_id as search_recipe_by_id_workflow,
    sync_with_mealie_result as sync_with_mealie_workflow,
)

app = typer.Typer()
P = ParamSpec("P")
T = TypeVar("T")


def _exit_with_error(message: str) -> NoReturn:
    rprint(f"[red]{message}[/red]")
    sys.exit(1)


def _run_or_exit(func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    try:
        return func(*args, **kwargs)
    except SettingsError as exc:
        render_settings_error(exc)
        sys.exit(1)
    except UserFacingError as exc:
        _exit_with_error(str(exc))


def _print_repository_warnings(invalid_entries: list[InvalidStoredRecipe]) -> None:
    if not invalid_entries:
        return
    noun = "recipe" if len(invalid_entries) == 1 else "recipes"
    rprint(
        "[yellow]Warning:[/yellow] skipped "
        f"{len(invalid_entries)} invalid stored {noun} from the local repository."
    )
    for entry in invalid_entries:
        label = entry.recipe_id or f"entry #{entry.position}"
        rprint(f"[yellow]- {label}: {entry.reason}[/yellow]")


@app.command(name="help")
def help_command(
    command: str | None = typer.Argument(
        None, help="Command to show help for (optional)."
    ),
    all_commands: bool = typer.Option(
        False, "--all", "-a", help="Show help for all commands."
    ),
):
    """
    Show help for the CLI or a specific command.
    """
    root_command = get_command(app)
    root_ctx = click.Context(root_command)
    if not isinstance(root_command, click.Group):
        typer.echo(root_command.get_help(root_ctx))
        return
    root_group = root_command

    if all_commands:
        for name, cmd in root_group.commands.items():
            typer.echo(
                cmd.get_help(click.Context(cmd, info_name=name, parent=root_ctx))
            )
            typer.echo("")
        return

    if command is None:
        typer.echo(root_command.get_help(root_ctx))
        typer.echo(
            "\nTip: run `kptncook help <command>` or `kptncook <command> --help` "
            "for command-specific options."
        )
        return

    selected_command = root_group.commands.get(command)
    if selected_command is None:
        _exit_with_error(f"Unknown command: {command}")
        return
    typer.echo(
        selected_command.get_help(
            click.Context(selected_command, info_name=command, parent=root_ctx)
        )
    )


@app.command(name="kptncook-today")
def list_kptncook_today():
    """
    List all recipes for today from the kptncook site.
    """
    recipes = _run_or_exit(get_today_recipes)
    for recipe in recipes:
        pprint(recipe)


@app.command(name="save-todays-recipes")
def save_todays_recipes():
    """
    Save recipes for today from kptncook site.
    """
    _run_or_exit(save_todays_recipes_workflow)


@app.command(name="dailies")
def list_kptncook_dailies(
    recipe_filter: str | None = typer.Option(
        None, "--recipe-filter", help="Filter daily recipes by recipeFilter value."
    ),
    zone: str | None = typer.Option(
        None, "--zone", help="Filter daily recipes by zone."
    ),
    is_subscribed: bool | None = typer.Option(
        None,
        "--subscribed/--not-subscribed",
        help="Filter daily recipes by subscription status.",
    ),
    save: bool = typer.Option(
        False,
        "--save",
        "-s",
        help="Save daily recipes to the local repository.",
    ),
):
    """
    List daily recipes from the kptncook site.
    """
    recipes = _run_or_exit(
        list_dailies_workflow,
        recipe_filter=recipe_filter,
        zone=zone,
        is_subscribed=is_subscribed,
    )
    if not recipes:
        rprint("No recipes found.")
        return
    if save:
        _run_or_exit(save_recipe_entries, recipes)
        rprint(f"Added {len(recipes)} recipes to local repository")
        return
    for recipe in recipes:
        pprint(recipe)


@app.command(name="sync-with-mealie")
def sync_with_mealie():
    """
    Sync locally saved recipes with mealie.
    """
    result = _run_or_exit(sync_with_mealie_workflow)
    _print_repository_warnings(result.invalid_repository_entries)
    rprint(f"Created {result.created_count} recipes")


@app.command(name="sync")
def sync():
    """
    Fetch recipes for today from api, save them to disk and sync with mealie
    afterwards.
    """
    save_todays_recipes()
    sync_with_mealie()


@app.command(name="backup-favorites")
def backup_kptncook_favorites():
    """
    Store kptncook favorites in local repository.
    """
    result = _run_or_exit(backup_kptncook_favorites_workflow)
    rprint(f"Found {result.favorite_count} favorites")
    rprint(f"Added {result.saved_count} recipes to local repository")


@app.command(name="kptncook-access-token")
def get_kptncook_access_token():
    """
    Get access token for kptncook.
    """
    access_token = _run_or_exit(get_kptncook_access_token_workflow)
    rprint("[green]✓ Access token retrieved successfully[/green]")
    rprint("Your access token: ", access_token)
    from kptncook.env import ENV_PATH

    rprint(f"Add it to {ENV_PATH} as KPTNCOOK_ACCESS_TOKEN=...")


@app.command(name="list-recipes")
def list_recipes():
    """
    List all locally saved recipes.
    """
    result = _run_or_exit(load_kptncook_recipes_from_repository)
    _print_repository_warnings(result.invalid_entries)
    for num, recipe in enumerate(result.recipes):
        title = localized_fallback(recipe.localized_title) or "Unknown title"
        rprint(num, title, recipe.id.oid)


@app.command(name="ls")
def list_recipes_alias():
    """
    Alias for list-recipes.
    """
    list_recipes()


@app.command(name="discovery-screen")
def list_discovery_screen(
    show_quick_search: bool = typer.Option(
        True, "--quick-search/--no-quick-search", help="Show quick search entries."
    ),
):
    """
    List discovery screen lists and quick search entries.
    """
    data = _run_or_exit(get_discovery_screen)

    if data.lists:
        rprint("Discovery lists:")
        for entry in data.lists:
            list_id = entry.list_id or "-"
            title = entry.title or "-"
            list_type = entry.list_type or "-"
            rprint(f"- {list_id} | {title} | {list_type}")
    else:
        rprint("No discovery lists found.")

    if show_quick_search:
        if data.quick_search:
            rprint("Quick search:")
            for label in data.quick_search:
                rprint(f"- {label}")
        else:
            rprint("No quick search entries found.")


@app.command(name="discovery-list")
def list_discovery_list(
    list_type: str = typer.Option(
        ...,
        "--list-type",
        "-t",
        help="Discovery list type (latest, recommended, curated, automated).",
    ),
    list_id: str | None = typer.Option(
        None,
        "--list-id",
        "-i",
        help="Discovery list id (required for curated/automated).",
    ),
    save: bool = typer.Option(
        False,
        "--save",
        "-s",
        help="Save discovery list recipes to the local repository.",
    ),
):
    """
    List recipes from a discovery list.
    """
    try:
        list_type = normalize_discovery_list_type(list_type)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    list_id = normalize_discovery_list_id(list_id)
    if list_type in DISCOVERY_LIST_TYPES_REQUIRE_ID and list_id is None:
        raise typer.BadParameter(
            "list-id is required when list-type is curated or automated"
        )

    recipes = _run_or_exit(
        get_discovery_list_recipes, list_type=list_type, list_id=list_id
    )
    if not recipes:
        rprint("No recipes found.")
        return

    if save:
        _run_or_exit(save_recipe_entries, recipes)
        rprint(f"Added {len(recipes)} recipes to local repository")
        return

    for recipe in recipes:
        pprint(recipe)


@app.command(name="ingredients-popular")
def list_popular_ingredients():
    """
    List popular ingredients.
    """
    ingredients = _run_or_exit(list_popular_ingredients_workflow)
    if not ingredients:
        rprint("No popular ingredients found.")
        return

    printed = 0
    for entry in ingredients:
        ingredient_id = _extract_ingredient_id(entry)
        name = _extract_ingredient_name(entry)
        if ingredient_id is None and name is None:
            continue
        if printed == 0:
            rprint("Popular ingredients:")
        printed += 1
        rprint(f"- {ingredient_id or '-'} | {name or '-'}")

    if printed == 0:
        rprint("No popular ingredients found.")


@app.command(name="recipes-with-ingredients")
def list_recipes_with_ingredients(
    ingredient_ids: list[str] = typer.Option(
        ...,
        "--ingredient-id",
        "-i",
        help="Ingredient id (repeatable, comma-separated ok).",
    ),
    save: bool = typer.Option(
        False,
        "--save",
        "-s",
        help="Save recipes to the local repository.",
    ),
):
    """
    List recipes that match ingredient ids.
    """
    ids = normalize_ingredient_ids(ingredient_ids)
    if not ids:
        _exit_with_error("Please provide one or more non-empty --ingredient-id values.")

    recipes = _run_or_exit(get_recipes_with_ingredients, ids)
    if not recipes:
        rprint("No recipes found.")
        return

    if save:
        _run_or_exit(save_recipe_entries, recipes)
        rprint(f"Added {len(recipes)} recipes to local repository")
        return

    for recipe in recipes:
        pprint(recipe)


@app.command(name="onboarding")
def list_onboarding_recipes(
    tags: list[str] = typer.Option(
        ..., "--tag", "-t", help="Onboarding tag (repeatable, comma-separated ok)."
    ),
    save: bool = typer.Option(
        False,
        "--save",
        "-s",
        help="Save onboarding recipes to the local repository.",
    ),
):
    """
    List onboarding recipes by tags.
    """
    tag_list = normalize_tags(tags)
    if not tag_list:
        _exit_with_error("Please provide one or more non-empty --tag values.")

    recipes = _run_or_exit(get_onboarding_recipes, tag_list)
    if not recipes:
        rprint("No onboarding recipes found.")
        return

    if save:
        _run_or_exit(save_recipe_entries, recipes)
        rprint(f"Added {len(recipes)} recipes to local repository")
        return

    for recipe in recipes:
        pprint(recipe)


@app.command(name="delete-recipes")
def delete_recipes(
    indices: list[int] | None = typer.Argument(
        None, help="Indices from list-recipes to delete."
    ),
    oids: list[str] | None = typer.Option(
        None, "--oid", "-o", help="Recipe oid to delete (repeatable)."
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
):
    """
    Delete recipes from the local repository.
    """
    index_list = indices or []
    oid_list = oids or []
    if not index_list and not oid_list:
        _exit_with_error("Please provide one or more recipe indices or --oid values.")

    result = _run_or_exit(
        delete_recipes_by_selection,
        indices=index_list,
        oids=oid_list,
    )
    _print_repository_warnings(result.invalid_repository_entries)

    if result.invalid_indices:
        rprint(
            "Invalid indices (out of range): "
            + ", ".join(str(i) for i in result.invalid_indices)
        )
    if result.missing_ids:
        rprint("Unknown recipe ids: " + ", ".join(result.missing_ids))
    if not result.to_delete_ids:
        _exit_with_error("No matching recipes to delete.")

    recipe_by_oid = {recipe.id.oid: recipe for recipe in result.recipes}
    rprint("Recipes to delete:")
    for oid in result.to_delete_ids:
        recipe = recipe_by_oid.get(oid)
        if recipe is None:
            rprint(f"- {oid}")
            continue
        title = localized_fallback(recipe.localized_title) or "Unknown title"
        rprint(f"- {title} ({oid})")

    if not force and not typer.confirm("Delete these recipes from local storage?"):
        _exit_with_error("Aborted.")

    deleted, missing = _run_or_exit(delete_repository_recipes, result.to_delete_ids)
    if missing:
        rprint("Some recipes were not found: " + ", ".join(missing))
    rprint(f"Deleted {len(deleted)} recipes.")


@app.command(name="search-by-id")
def search_kptncook_recipe_by_id(id_: str):
    """
    Search for a recipe by id in kptncook api and add it to the local repository.
    """
    result = _run_or_exit(search_recipe_by_id_workflow, id_)
    rprint(result.id_type, result.id_value)
    rprint(f"Added recipe {result.id_type} {result.id_value} to local repository")


OptionalId = Optional[str]


@app.command(name="export-recipes-to-paprika")
def export_recipes_to_paprika(_id: OptionalId = typer.Argument(None)):
    """
    Export one recipe or all recipes to Paprika app.
    """
    result = _run_or_exit(export_recipes_to_paprika_workflow, _id)
    _print_repository_warnings(result.invalid_repository_entries)
    rprint(
        "\n The data was exported to '%s'. Open the export file with the Paprika App.\n"
        % result.filename
    )


@app.command(name="export-recipes-to-tandoor")
def export_recipes_to_tandoor(_id: OptionalId = typer.Argument(None)):
    """
    Export one recipe or all recipes to Tandoor.
    """
    result = _run_or_exit(export_recipes_to_tandoor_workflow, _id)
    _print_repository_warnings(result.invalid_repository_entries)
    rprint(
        "\n The data was exported to '%s'. Open the export file with Tandoor.\n"
        % ", ".join(result.filenames)
    )
