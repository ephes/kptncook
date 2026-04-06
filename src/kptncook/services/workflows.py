from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from kptncook.api import KptnCookClient, _collect_recipe_identifiers, parse_id
from kptncook.config import settings
from kptncook.env import ENV_PATH
from kptncook.http_errors import (
    UserFacingError,
    extract_mealie_detail_message,
    format_http_status_error,
    format_request_error,
)
from kptncook.mealie import MealieApiClient, kptncook_to_mealie
from kptncook.models import Recipe
from kptncook.paprika import PaprikaExporter
from kptncook.password_manager import get_credentials
from kptncook.repositories import RecipeInDb
from kptncook.services.discovery import DiscoveryScreenData, parse_discovery_screen
from kptncook.services.repository import (
    delete_recipe_ids,
    get_repository_recipe_by_oid,
    list_repository_ids,
    list_repository_recipes,
    repository_needs_sync,
    save_recipe_entries,
)
from kptncook.tandoor import TandoorExporter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FavoritesBackupResult:
    favorite_count: int
    saved_count: int


@dataclass(frozen=True)
class SearchResult:
    id_type: str
    id_value: str
    recipe: RecipeInDb


def get_today_recipes() -> list[RecipeInDb]:
    return KptnCookClient().list_today()


def save_todays_recipes() -> int:
    if not repository_needs_sync(date.today()):
        return 0
    recipes = get_today_recipes()
    return save_recipe_entries(recipes)


def get_mealie_client() -> MealieApiClient:
    client = MealieApiClient(settings.mealie_url)
    try:
        if settings.mealie_api_token:
            client.login_with_token(settings.mealie_api_token)
            return client
        if settings.mealie_username and settings.mealie_password:
            client.login(settings.mealie_username, settings.mealie_password)
            return client
    except Exception as exc:
        raise UserFacingError(f"Could not login to mealie: {exc}") from exc
    raise UserFacingError(
        "Mealie authentication required. "
        "Set MEALIE_API_TOKEN or MEALIE_USERNAME/MEALIE_PASSWORD."
    )


def get_kptncook_recipes_from_mealie(client: MealieApiClient) -> list[Any]:
    recipes = client.get_all_recipes()
    recipes_with_details = [client.get_via_slug(recipe.slug) for recipe in recipes]
    return [r for r in recipes_with_details if r.extras.get("source") == "kptncook"]


def get_kptncook_recipes_from_repository():
    return list_repository_recipes()


def get_recipe_from_repository_by_oid(oid: str):
    return get_repository_recipe_by_oid(oid=oid)


def _resolve_recipe_summaries(
    client: KptnCookClient, items: Sequence[object], *, action: str
) -> list[RecipeInDb]:
    if not items:
        return []
    try:
        return client.resolve_recipe_summaries(items)
    except httpx.HTTPStatusError as exc:
        raise UserFacingError(
            format_http_status_error(exc.response, action=action)
        ) from exc
    except httpx.HTTPError as exc:
        raise UserFacingError(format_request_error(exc)) from exc


def list_dailies(
    *,
    recipe_filter: str | None = None,
    zone: str | None = None,
    is_subscribed: bool | None = None,
) -> list[RecipeInDb]:
    try:
        return KptnCookClient().list_dailies(
            recipe_filter=recipe_filter,
            zone=zone,
            is_subscribed=is_subscribed,
        )
    except httpx.HTTPStatusError as exc:
        raise UserFacingError(
            format_http_status_error(exc.response, action="fetching dailies")
        ) from exc
    except httpx.HTTPError as exc:
        raise UserFacingError(format_request_error(exc)) from exc


def _require_access_token() -> None:
    if settings.kptncook_access_token is None:
        raise UserFacingError(
            f"Please set KPTNCOOK_ACCESS_TOKEN in your environment or {ENV_PATH}"
        )


def sync_with_mealie() -> int:
    client = get_mealie_client()
    kptncook_recipes_from_mealie = get_kptncook_recipes_from_mealie(client)
    recipes = get_kptncook_recipes_from_repository()
    kptncook_recipes_from_repository = [kptncook_to_mealie(r) for r in recipes]
    ids_in_mealie = {r.extras["kptncook_id"] for r in kptncook_recipes_from_mealie}
    ids_from_api = {r.extras["kptncook_id"] for r in kptncook_recipes_from_repository}
    ids_to_add = ids_from_api - ids_in_mealie
    recipes_to_add = [
        recipe
        for recipe in kptncook_recipes_from_repository
        if recipe.extras.get("kptncook_id") in ids_to_add
    ]
    created_slugs: list[str] = []
    for recipe in recipes_to_add:
        try:
            created = client.create_recipe(recipe)
            created_slugs.append(created.slug)
        except httpx.HTTPStatusError as exc:
            detail_message = extract_mealie_detail_message(exc.response)
            if detail_message == "Recipe already exists":
                continue
            logger.warning(
                "Failed to create recipe %s in Mealie (%s): %s",
                recipe.name,
                exc.response.status_code,
                detail_message or exc,
            )
    return len(created_slugs)


def backup_kptncook_favorites() -> FavoritesBackupResult:
    _require_access_token()
    client = KptnCookClient()
    try:
        favorites = client.list_favorites()
    except httpx.HTTPStatusError as exc:
        raise UserFacingError(
            format_http_status_error(
                exc.response,
                action="fetching favorites",
                unavailable_on_redirect=True,
            )
        ) from exc
    except httpx.HTTPError as exc:
        raise UserFacingError(format_request_error(exc)) from exc
    except ValueError as exc:
        raise UserFacingError(str(exc)) from exc

    identifiers = _collect_recipe_identifiers(favorites)
    if not identifiers:
        raise UserFacingError("Could not find any favorites")

    recipes = _resolve_recipe_summaries(client, identifiers, action="resolving recipes")
    if len(recipes) == 0:
        raise UserFacingError("Could not find any favorites")

    saved_count = save_recipe_entries(recipes)
    return FavoritesBackupResult(
        favorite_count=len(favorites),
        saved_count=saved_count,
    )


def get_kptncook_access_token() -> str:
    username, password = get_credentials(
        username_command=settings.kptncook_username_command,
        password_command=settings.kptncook_password_command,
    )
    if not username or not password:
        raise UserFacingError("Failed to get credentials")

    client = KptnCookClient()
    try:
        return client.get_access_token(username, password)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            message = (
                "Login failed (HTTP 401). Check your email/password and make sure "
                "KPTNCOOK_API_KEY is set to your real API key (not a placeholder)."
            )
        else:
            message = format_http_status_error(
                exc.response, action="getting access token"
            )
        raise UserFacingError(message) from exc
    except httpx.HTTPError as exc:
        raise UserFacingError(format_request_error(exc)) from exc


def get_discovery_screen() -> DiscoveryScreenData:
    try:
        payload = KptnCookClient().get_discovery_screen()
    except httpx.HTTPStatusError as exc:
        raise UserFacingError(
            format_http_status_error(exc.response, action="fetching discovery screen")
        ) from exc
    except httpx.HTTPError as exc:
        raise UserFacingError(format_request_error(exc)) from exc
    return parse_discovery_screen(payload)


def get_discovery_list_recipes(
    *, list_type: str, list_id: str | None
) -> list[RecipeInDb]:
    client = KptnCookClient()
    try:
        items = client.get_discovery_list(list_type=list_type, list_id=list_id)
    except httpx.HTTPStatusError as exc:
        raise UserFacingError(
            format_http_status_error(exc.response, action="fetching discovery list")
        ) from exc
    except httpx.HTTPError as exc:
        raise UserFacingError(format_request_error(exc)) from exc
    return _resolve_recipe_summaries(client, items, action="resolving recipes")


def list_popular_ingredients() -> list[dict[str, object]]:
    _require_access_token()
    client = KptnCookClient()
    try:
        return client.list_popular_ingredients()
    except httpx.HTTPStatusError as exc:
        raise UserFacingError(
            format_http_status_error(
                exc.response, action="fetching popular ingredients"
            )
        ) from exc
    except httpx.HTTPError as exc:
        raise UserFacingError(format_request_error(exc)) from exc


def get_recipes_with_ingredients(ingredient_ids: list[str]) -> list[RecipeInDb]:
    _require_access_token()
    client = KptnCookClient()
    try:
        items = client.get_recipes_with_ingredients(ingredient_ids=ingredient_ids)
    except httpx.HTTPStatusError as exc:
        raise UserFacingError(
            format_http_status_error(
                exc.response,
                action="fetching recipes with ingredients",
            )
        ) from exc
    except httpx.HTTPError as exc:
        raise UserFacingError(format_request_error(exc)) from exc
    return _resolve_recipe_summaries(client, items, action="resolving recipes")


def get_onboarding_recipes(tags: list[str]) -> list[RecipeInDb]:
    client = KptnCookClient()
    try:
        items = client.get_onboarding_recipes(tags=tags)
    except httpx.HTTPStatusError as exc:
        raise UserFacingError(
            format_http_status_error(exc.response, action="fetching onboarding recipes")
        ) from exc
    except httpx.HTTPError as exc:
        raise UserFacingError(format_request_error(exc)) from exc
    return _resolve_recipe_summaries(client, items, action="resolving recipes")


def delete_recipes_by_selection(
    *,
    indices: list[int],
    oids: list[str],
) -> tuple[list[Recipe], list[int], list[str], list[str]]:
    recipes = get_kptncook_recipes_from_repository()
    index_ids: list[str] = []
    invalid_indices: list[int] = []
    for index in indices:
        if index < 0 or index >= len(recipes):
            invalid_indices.append(index)
            continue
        index_ids.append(recipes[index].id.oid)

    requested_ids: list[str] = []
    for oid in index_ids + oids:
        if oid not in requested_ids:
            requested_ids.append(oid)

    existing_ids = {str(key) for key in list_repository_ids().keys()}
    missing_ids = [oid for oid in requested_ids if str(oid) not in existing_ids]
    to_delete_ids = [oid for oid in requested_ids if str(oid) in existing_ids]
    return recipes, invalid_indices, missing_ids, to_delete_ids


def delete_repository_recipes(ids: list[str]) -> tuple[list[str], list[str]]:
    return delete_recipe_ids(ids)


def search_recipe_by_id(id_: str) -> SearchResult:
    resolved_id = id_
    if resolved_id.startswith("https://share.kptncook.com/"):
        try:
            response = httpx.get(resolved_id)
        except httpx.HTTPError as exc:
            raise UserFacingError(
                f"Request failed while resolving share URL: {exc}"
            ) from exc
        if response.status_code not in (301, 302):
            raise UserFacingError(
                f"Could not get redirect location (HTTP {response.status_code})."
            )
        location = response.headers.get("location")
        if not location:
            raise UserFacingError("Share URL did not include a redirect location.")
        resolved_id = location

    parsed = parse_id(resolved_id)
    if parsed is None:
        raise UserFacingError("Could not parse id")

    id_type, id_value = parsed
    try:
        recipes = KptnCookClient().get_by_ids([(id_type, id_value)])
    except httpx.HTTPStatusError as exc:
        raise UserFacingError(
            format_http_status_error(exc.response, action="fetching recipe")
        ) from exc
    except httpx.HTTPError as exc:
        raise UserFacingError(format_request_error(exc)) from exc

    if len(recipes) == 0:
        raise UserFacingError("Could not find recipe")

    recipe = recipes[0]
    save_recipe_entries([recipe])
    return SearchResult(id_type=id_type, id_value=id_value, recipe=recipe)


def get_recipe_by_id(id_: str):
    parsed = parse_id(id_)
    if parsed is None:
        raise UserFacingError("Could not parse id")
    _, id_value = parsed
    found_recipes = get_recipe_from_repository_by_oid(oid=id_value)
    if len(found_recipes) == 0:
        raise UserFacingError("Recipe not found.")
    if len(found_recipes) > 1:
        raise UserFacingError("More than one recipe found with that ID.")
    return found_recipes


def export_recipes_to_paprika(recipe_id: str | None) -> str:
    recipes = (
        get_recipe_by_id(recipe_id)
        if recipe_id
        else get_kptncook_recipes_from_repository()
    )
    return PaprikaExporter().export(recipes=recipes)


def export_recipes_to_tandoor(recipe_id: str | None) -> list[str]:
    recipes = (
        get_recipe_by_id(recipe_id)
        if recipe_id
        else get_kptncook_recipes_from_repository()
    )
    return TandoorExporter().export(recipes=recipes)
