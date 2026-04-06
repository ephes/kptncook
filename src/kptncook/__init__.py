"""
kptncook is a little command line utility to download
new recipes.
"""

from importlib.metadata import PackageNotFoundError, version

import httpx

from .api import KptnCookClient, _collect_recipe_identifiers, parse_id
from .cli import (
    OptionalId,
    app as cli,
    backup_kptncook_favorites,
    delete_recipes,
    export_recipes_to_paprika,
    export_recipes_to_tandoor,
    get_kptncook_access_token,
    help_command,
    list_discovery_list,
    list_discovery_screen,
    list_kptncook_dailies,
    list_kptncook_today,
    list_onboarding_recipes,
    list_popular_ingredients,
    list_recipes,
    list_recipes_alias,
    list_recipes_with_ingredients,
    save_todays_recipes,
    search_kptncook_recipe_by_id,
    sync,
    sync_with_mealie,
)
from .http_errors import extract_mealie_detail_message as _extract_mealie_detail_message
from .mealie import MealieApiClient
from .services.discovery import (
    _extract_ingredient_name,
    _extract_quick_search_entries,
)
from .services.workflows import (
    _require_access_token,
    get_kptncook_recipes_from_repository,
    get_mealie_client,
    get_recipe_by_id,
    get_recipe_from_repository_by_oid,
)

try:
    __version__ = version("kptncook")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "__version__",
    "KptnCookClient",
    "MealieApiClient",
    "OptionalId",
    "_collect_recipe_identifiers",
    "_extract_ingredient_name",
    "_extract_mealie_detail_message",
    "_extract_quick_search_entries",
    "_require_access_token",
    "backup_kptncook_favorites",
    "cli",
    "delete_recipes",
    "export_recipes_to_paprika",
    "export_recipes_to_tandoor",
    "get_kptncook_access_token",
    "get_kptncook_recipes_from_repository",
    "get_mealie_client",
    "get_recipe_by_id",
    "get_recipe_from_repository_by_oid",
    "help_command",
    "httpx",
    "list_discovery_list",
    "list_discovery_screen",
    "list_kptncook_dailies",
    "list_kptncook_today",
    "list_onboarding_recipes",
    "list_popular_ingredients",
    "list_recipes",
    "list_recipes_alias",
    "list_recipes_with_ingredients",
    "parse_id",
    "save_todays_recipes",
    "search_kptncook_recipe_by_id",
    "sync",
    "sync_with_mealie",
]
