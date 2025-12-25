"""
kptncook is a little command line utility to download
new recipes.
"""

import sys
from datetime import date
from typing import Any, Optional

import httpx
import typer
from rich import print as rprint
from rich.pretty import pprint

from .api import KptnCookClient, parse_id
from .config import settings
from .mealie import MealieApiClient, kptncook_to_mealie
from .models import Recipe, localized_fallback
from .paprika import PaprikaExporter
from .tandoor import TandoorExporter
from .repositories import RecipeInDb, RecipeRepository

__all__ = [
    "list_kptncook_today",
    "list_kptncook_dailies",
    "save_todays_recipes",
    "sync_with_mealie",
    "sync",
    "backup_kptncook_favorites",
    "get_kptncook_access_token",
    "list_recipes",
    "delete_recipes",
    "search_kptncook_recipe_by_id",
    "list_discovery_screen",
    "list_discovery_list",
    "list_onboarding_recipes",
    "list_popular_ingredients",
    "list_recipes_with_ingredients",
    "export_recipes_to_paprika",
    "export_recipes_to_tandoor",
]

__version__ = "0.0.26"
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


@cli.command(name="dailies")
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
    client = KptnCookClient()
    try:
        recipes = client.list_dailies(
            recipe_filter=recipe_filter,
            zone=zone,
            is_subscribed=is_subscribed,
        )
    except httpx.HTTPStatusError as exc:
        detail = _extract_http_error_message(exc.response)
        message = f"HTTP {exc.response.status_code} while fetching dailies"
        if detail:
            message = f"{message}: {detail}"
        rprint(f"[red]{message}[/red]")
        sys.exit(1)
    except httpx.HTTPError as exc:
        rprint(f"[red]Request failed: {exc}[/red]")
        sys.exit(1)
    if not recipes:
        rprint("No recipes found.")
        return
    for recipe in recipes:
        pprint(recipe)
    if save:
        fs_repo = RecipeRepository(settings.root)
        fs_repo.add_list(recipes)
        rprint(f"Added {len(recipes)} recipes to local repository")


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
        except Exception:
            continue
    return recipes


def get_recipe_from_repository_by_oid(oid: str) -> list[Recipe]:
    """
    get one single recipe from local repository
    :param oid: oid of recipe
    :return: list
    """
    recipes = get_kptncook_recipes_from_repository()
    return [recipe for num, recipe in enumerate(recipes) if recipe.id.oid == oid]


def _extract_mealie_detail_message(response: httpx.Response) -> str | None:
    if "application/json" not in response.headers.get("content-type", ""):
        return None
    try:
        response_data = response.json()
    except ValueError:
        return None
    if isinstance(response_data, dict):
        detail = response_data.get("detail", {})
        if isinstance(detail, dict):
            detail_message = detail.get("message")
            if isinstance(detail_message, str):
                return detail_message
    return None


def _extract_http_error_message(response: httpx.Response) -> str | None:
    if "application/json" not in response.headers.get("content-type", ""):
        return None
    try:
        response_data = response.json()
    except ValueError:
        return None
    if isinstance(response_data, dict):
        for key in ("message", "error"):
            value = response_data.get(key)
            if isinstance(value, str):
                return value
        detail = response_data.get("detail")
        if isinstance(detail, dict):
            detail_message = detail.get("message")
            if isinstance(detail_message, str):
                return detail_message
    return None


def _resolve_recipe_summaries(
    client: KptnCookClient, items: list[object]
) -> list[RecipeInDb]:
    if not items:
        return []
    try:
        return client.resolve_recipe_summaries(items)
    except httpx.HTTPStatusError as exc:
        detail = _extract_http_error_message(exc.response)
        message = f"HTTP {exc.response.status_code} while resolving recipes"
        if detail:
            message = f"{message}: {detail}"
        rprint(f"[red]{message}[/red]")
        sys.exit(1)
    except httpx.HTTPError as exc:
        rprint(f"[red]Request failed: {exc}[/red]")
        sys.exit(1)


def _extract_nested_list(payload: object, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
        for value in payload.values():
            if isinstance(value, dict):
                nested = _extract_nested_list(value, keys)
                if nested:
                    return nested
    return []


def _extract_localized_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("de", "en", "es", "fr", "pt"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        for key in ("singular", "plural", "uncountable"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        for candidate in value.values():
            if isinstance(candidate, str):
                return candidate
    return None


def _coerce_discovery_list_id(value: object) -> str | None:
    if isinstance(value, dict):
        if "$oid" in value:
            value = value["$oid"]
        elif "oid" in value:
            value = value["oid"]
    if isinstance(value, str):
        return value
    if value is None:
        return None
    return str(value)


def _extract_discovery_list_entries(payload: object) -> list[dict[str, Any]]:
    candidates = _extract_nested_list(
        payload, ("lists", "discoveryLists", "discoveryList", "items", "sections")
    )
    return [item for item in candidates if isinstance(item, dict)]


def _extract_discovery_list_id(entry: dict[str, Any]) -> str | None:
    for key in ("id", "_id", "listId", "list_id", "oid"):
        if key in entry:
            return _coerce_discovery_list_id(entry.get(key))
    return None


def _coerce_ingredient_id(value: object) -> str | None:
    if isinstance(value, dict):
        if "$oid" in value:
            value = value["$oid"]
        elif "oid" in value:
            value = value["oid"]
    if isinstance(value, str):
        return value
    if value is None:
        return None
    return str(value)


def _extract_ingredient_id(entry: dict[str, Any]) -> str | None:
    for key in ("_id", "id", "ingredientId", "ingredient_id", "oid"):
        if key in entry:
            return _coerce_ingredient_id(entry.get(key))
    return None


def _extract_ingredient_name(entry: dict[str, Any]) -> str | None:
    for key in ("numberTitle", "localizedTitle", "name", "title", "label"):
        if key in entry:
            name = _extract_localized_text(entry.get(key))
            if name:
                return name
    return None


def _extract_discovery_list_title(entry: dict[str, Any]) -> str | None:
    for key in ("title", "localizedTitle", "name"):
        if key in entry:
            title = _extract_localized_text(entry.get(key))
            if title:
                return title
    return None


def _extract_discovery_list_type(entry: dict[str, Any]) -> str | None:
    for key in ("listType", "list_type", "type"):
        value = entry.get(key)
        if isinstance(value, str):
            return value
    return None


def _extract_quick_search_entries(payload: object) -> list[Any]:
    return _extract_nested_list(
        payload,
        (
            "quickSearchEntries",
            "quickSearch",
            "quick_search",
            "quicksearch",
        ),
    )


def _format_quick_search_entry(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("title", "name", "label", "text", "query"):
            value = entry.get(key)
            if isinstance(value, str):
                return value
        localized = entry.get("localizedTitle") or entry.get("title")
        return _extract_localized_text(localized)
    return None


_DISCOVERY_LIST_TYPES = {"latest", "recommended", "curated", "automated"}
_DISCOVERY_LIST_TYPES_REQUIRE_ID = {"curated", "automated"}


def _normalize_discovery_list_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _DISCOVERY_LIST_TYPES:
        raise typer.BadParameter(
            "list-type must be one of: latest, recommended, curated, automated"
        )
    return normalized


def _normalize_discovery_list_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_tags(tags: list[str]) -> list[str]:
    return _normalize_list_input(tags)


def _normalize_list_input(items: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        for raw in item.split(","):
            candidate = raw.strip()
            if not candidate or candidate in seen:
                continue
            normalized.append(candidate)
            seen.add(candidate)
    return normalized


def _normalize_ingredient_ids(ingredient_ids: list[str]) -> list[str]:
    return _normalize_list_input(ingredient_ids)


def _require_access_token() -> None:
    if settings.kptncook_access_token is None:
        rprint("Please set KPTNCOOK_ACCESS_TOKEN in your environment or .env file")
        sys.exit(1)


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
            detail_message = _extract_mealie_detail_message(e.response)
            if detail_message == "Recipe already exists":
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
    _require_access_token()
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

    Credentials can be retrieved from a password manager by setting:
    - KPTNCOOK_USERNAME_COMMAND: Command to retrieve username
    - KPTNCOOK_PASSWORD_COMMAND: Command to retrieve password

    Example for 1Password:
    KPTNCOOK_USERNAME_COMMAND="op read op://Personal/KptnCook/username"
    KPTNCOOK_PASSWORD_COMMAND="op read op://Personal/KptnCook/password"
    """
    from .password_manager import get_credentials

    username, password = get_credentials(
        username_command=settings.kptncook_username_command,
        password_command=settings.kptncook_password_command,
    )

    if not username or not password:
        rprint("[red]Failed to get credentials[/red]")
        sys.exit(1)

    client = KptnCookClient()
    try:
        access_token = client.get_access_token(username, password)
        rprint("[green]✓ Access token retrieved successfully[/green]")
        rprint("Your access token: ", access_token)
    except Exception as e:
        rprint(f"[red]Failed to get access token: {e}[/red]")
        sys.exit(1)


@cli.command(name="list-recipes")
def list_recipes():
    """
    List all locally saved recipes.
    """
    recipes = get_kptncook_recipes_from_repository()
    for num, recipe in enumerate(recipes):
        title = localized_fallback(recipe.localized_title) or "Unknown title"
        rprint(num, title, recipe.id.oid)


@cli.command(name="discovery-screen")
def list_discovery_screen(
    show_quick_search: bool = typer.Option(
        True, "--quick-search/--no-quick-search", help="Show quick search entries."
    ),
):
    """
    List discovery screen lists and quick search entries.
    """
    client = KptnCookClient()
    try:
        payload = client.get_discovery_screen()
    except httpx.HTTPStatusError as exc:
        detail = _extract_http_error_message(exc.response)
        message = f"HTTP {exc.response.status_code} while fetching discovery screen"
        if detail:
            message = f"{message}: {detail}"
        rprint(f"[red]{message}[/red]")
        sys.exit(1)
    except httpx.HTTPError as exc:
        rprint(f"[red]Request failed: {exc}[/red]")
        sys.exit(1)

    list_entries = _extract_discovery_list_entries(payload)
    list_printed = 0
    for entry in list_entries:
        list_id = _extract_discovery_list_id(entry)
        title = _extract_discovery_list_title(entry)
        list_type = _extract_discovery_list_type(entry)
        if list_id is None and title is None and list_type is None:
            continue
        if list_printed == 0:
            rprint("Discovery lists:")
        list_printed += 1
        list_id = list_id or "-"
        title = title or "-"
        list_type = list_type or "-"
        rprint(f"- {list_id} | {title} | {list_type}")
    if list_printed == 0:
        rprint("No discovery lists found.")

    if show_quick_search:
        quick_search = _extract_quick_search_entries(payload)
        quick_printed = 0
        for entry in quick_search:
            label = _format_quick_search_entry(entry)
            if label is None:
                continue
            if quick_printed == 0:
                rprint("Quick search:")
            quick_printed += 1
            rprint(f"- {label}")
        if quick_printed == 0:
            rprint("No quick search entries found.")


@cli.command(name="discovery-list")
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
    list_type = _normalize_discovery_list_type(list_type)
    list_id = _normalize_discovery_list_id(list_id)
    if list_type in _DISCOVERY_LIST_TYPES_REQUIRE_ID and list_id is None:
        raise typer.BadParameter(
            "list-id is required when list-type is curated or automated"
        )

    client = KptnCookClient()
    try:
        items = client.get_discovery_list(list_type=list_type, list_id=list_id)
    except httpx.HTTPStatusError as exc:
        detail = _extract_http_error_message(exc.response)
        message = f"HTTP {exc.response.status_code} while fetching discovery list"
        if detail:
            message = f"{message}: {detail}"
        rprint(f"[red]{message}[/red]")
        sys.exit(1)
    except httpx.HTTPError as exc:
        rprint(f"[red]Request failed: {exc}[/red]")
        sys.exit(1)

    if not items:
        rprint("No discovery list recipes found.")
        return

    recipes = _resolve_recipe_summaries(client, items)
    if not recipes:
        rprint("No recipes found.")
        return

    for recipe in recipes:
        pprint(recipe)

    if save:
        fs_repo = RecipeRepository(settings.root)
        fs_repo.add_list(recipes)
        rprint(f"Added {len(recipes)} recipes to local repository")


@cli.command(name="ingredients-popular")
def list_popular_ingredients():
    """
    List popular ingredients.
    """
    _require_access_token()
    client = KptnCookClient()
    try:
        ingredients = client.list_popular_ingredients()
    except httpx.HTTPStatusError as exc:
        detail = _extract_http_error_message(exc.response)
        message = f"HTTP {exc.response.status_code} while fetching popular ingredients"
        if detail:
            message = f"{message}: {detail}"
        rprint(f"[red]{message}[/red]")
        sys.exit(1)
    except httpx.HTTPError as exc:
        rprint(f"[red]Request failed: {exc}[/red]")
        sys.exit(1)

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
        ingredient_id = ingredient_id or "-"
        name = name or "-"
        rprint(f"- {ingredient_id} | {name}")

    if printed == 0:
        rprint("No popular ingredients found.")


@cli.command(name="recipes-with-ingredients")
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
    _require_access_token()
    ids = _normalize_ingredient_ids(ingredient_ids)
    if not ids:
        rprint("Please provide one or more non-empty --ingredient-id values.")
        sys.exit(1)

    client = KptnCookClient()
    try:
        items = client.get_recipes_with_ingredients(ingredient_ids=ids)
    except httpx.HTTPStatusError as exc:
        detail = _extract_http_error_message(exc.response)
        message = (
            f"HTTP {exc.response.status_code} while fetching recipes with ingredients"
        )
        if detail:
            message = f"{message}: {detail}"
        rprint(f"[red]{message}[/red]")
        sys.exit(1)
    except httpx.HTTPError as exc:
        rprint(f"[red]Request failed: {exc}[/red]")
        sys.exit(1)

    if not items:
        rprint("No recipes found.")
        return

    recipes = _resolve_recipe_summaries(client, items)
    if not recipes:
        rprint("No recipes found.")
        return

    for recipe in recipes:
        pprint(recipe)

    if save:
        fs_repo = RecipeRepository(settings.root)
        fs_repo.add_list(recipes)
        rprint(f"Added {len(recipes)} recipes to local repository")


@cli.command(name="onboarding")
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
    tag_list = _normalize_tags(tags)
    if not tag_list:
        rprint("Please provide one or more non-empty --tag values.")
        sys.exit(1)

    client = KptnCookClient()
    try:
        items = client.get_onboarding_recipes(tags=tag_list)
    except httpx.HTTPStatusError as exc:
        detail = _extract_http_error_message(exc.response)
        message = f"HTTP {exc.response.status_code} while fetching onboarding recipes"
        if detail:
            message = f"{message}: {detail}"
        rprint(f"[red]{message}[/red]")
        sys.exit(1)
    except httpx.HTTPError as exc:
        rprint(f"[red]Request failed: {exc}[/red]")
        sys.exit(1)

    if not items:
        rprint("No onboarding recipes found.")
        return

    recipes = _resolve_recipe_summaries(client, items)
    if not recipes:
        rprint("No recipes found.")
        return

    for recipe in recipes:
        pprint(recipe)

    if save:
        fs_repo = RecipeRepository(settings.root)
        fs_repo.add_list(recipes)
        rprint(f"Added {len(recipes)} recipes to local repository")


@cli.command(name="delete-recipes")
def delete_recipes(
    indices: Optional[list[int]] = typer.Argument(
        None, help="Indices from list-recipes to delete."
    ),
    oids: Optional[list[str]] = typer.Option(
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
        rprint("Please provide one or more recipe indices or --oid values.")
        sys.exit(1)

    recipes = get_kptncook_recipes_from_repository()
    index_ids = []
    invalid_indices = []
    for index in index_list:
        if index < 0 or index >= len(recipes):
            invalid_indices.append(index)
            continue
        index_ids.append(recipes[index].id.oid)

    requested_ids = []
    for oid in index_ids + oid_list:
        if oid not in requested_ids:
            requested_ids.append(oid)

    repo = RecipeRepository(settings.root)
    existing_by_id = repo.list_by_id()
    existing_ids = {str(key) for key in existing_by_id.keys()}

    missing_ids = [oid for oid in requested_ids if str(oid) not in existing_ids]
    to_delete_ids = [oid for oid in requested_ids if str(oid) in existing_ids]

    if invalid_indices:
        rprint(
            "Invalid indices (out of range): "
            + ", ".join(str(i) for i in invalid_indices)
        )
    if missing_ids:
        rprint("Unknown recipe ids: " + ", ".join(missing_ids))

    if not to_delete_ids:
        rprint("No matching recipes to delete.")
        sys.exit(1)

    recipe_by_oid = {recipe.id.oid: recipe for recipe in recipes}
    rprint("Recipes to delete:")
    for oid in to_delete_ids:
        recipe = recipe_by_oid.get(oid)
        if recipe is None:
            rprint(f"- {oid}")
            continue
        title = localized_fallback(recipe.localized_title) or "Unknown title"
        rprint(f"- {title} ({oid})")

    if not force and not typer.confirm("Delete these recipes from local storage?"):
        rprint("Aborted.")
        sys.exit(1)

    deleted, missing = repo.delete_by_ids(to_delete_ids)
    if missing:
        rprint("Some recipes were not found: " + ", ".join(missing))
    rprint(f"Deleted {len(deleted)} recipes.")


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


@cli.command(name="export-recipes-to-tandoor")
def export_recipes_to_tandoor(_id: OptionalId = typer.Argument(None)):
    """
    Export one recipe or all recipes to Tandoor

    Example usage 1:  kptncook  export-recipes-to-tandoor 635a68635100007500061cd7
    Example usage 2:  kptncook  export-recipes-to-tandoor
    """
    if _id:
        recipes = get_recipe_by_id(_id)
    else:
        recipes = get_kptncook_recipes_from_repository()
    exporter = TandoorExporter()
    filenames = exporter.export(recipes=recipes)
    rprint(
        "\n The data was exported to '%s'. Open the export file with Tandoor.\n"
        % ", ".join(filenames)
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
