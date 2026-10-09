from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from kptncook.api import KptnCookClient, _collect_recipe_identifiers, parse_id
from kptncook.config import get_settings
from kptncook.env import ENV_PATH
from kptncook.http_errors import (
    UserFacingError,
    format_http_status_error,
    format_request_error,
)
from kptncook.markdown_exporter import MarkdownExporter
from kptncook.mealie import MealieApiClient, kptncook_to_mealie
from kptncook.models import Recipe, localized_fallback
from kptncook.paprika import PaprikaExporter
from kptncook.password_manager import get_credentials
from kptncook.repositories import RecipeInDb
from kptncook.services.discovery import DiscoveryScreenData, parse_discovery_screen
from kptncook.services.repository import (
    InvalidStoredRecipe,
    RepositoryRecipesResult,
    RepositoryServiceError,
    delete_recipe_ids,
    load_repository_recipes,
    list_repository_ids,
    repository_needs_sync,
    save_recipe_entries,
)
from kptncook.tandoor import TandoorExporter

logger = logging.getLogger(__name__)
SHARE_URL_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@dataclass(frozen=True)
class FavoritesBackupResult:
    favorite_count: int
    saved_count: int


@dataclass(frozen=True)
class SearchResult:
    id_type: str
    id_value: str
    recipe: RecipeInDb


@dataclass(frozen=True)
class MealieSyncIssue:
    name: str
    reason: str


@dataclass(frozen=True)
class MealieSyncFailure:
    recipe_id: str
    recipe_name: str
    reason: str


@dataclass(frozen=True)
class SyncWithMealieResult:
    created_count: int
    invalid_repository_entries: list[InvalidStoredRecipe]
    # Keep main's constructor order for callers using positional arguments.
    failed: list[MealieSyncIssue] = field(default_factory=list)
    skipped_existing: list[MealieSyncIssue] = field(default_factory=list)
    failed_recipes: list[MealieSyncFailure] = field(default_factory=list)


@dataclass(frozen=True)
class DeleteSelectionResult:
    recipes: list[Recipe]
    invalid_indices: list[int]
    missing_ids: list[str]
    to_delete_ids: list[str]
    invalid_repository_entries: list[InvalidStoredRecipe]


@dataclass(frozen=True)
class PaprikaExportResult:
    filename: str
    invalid_repository_entries: list[InvalidStoredRecipe]


@dataclass(frozen=True)
class TandoorExportResult:
    filenames: list[str]
    invalid_repository_entries: list[InvalidStoredRecipe]


@dataclass(frozen=True)
class MarkdownExportResult:
    filenames: list[str]
    invalid_repository_entries: list[InvalidStoredRecipe]


def _wrap_repository_error(exc: RepositoryServiceError) -> UserFacingError:
    return UserFacingError(str(exc))


def load_kptncook_recipes_from_repository() -> RepositoryRecipesResult:
    try:
        return load_repository_recipes()
    except RepositoryServiceError as exc:
        raise _wrap_repository_error(exc) from exc


def load_recipe_from_repository_by_oid(oid: str) -> RepositoryRecipesResult:
    result = load_kptncook_recipes_from_repository()
    return RepositoryRecipesResult(
        recipes=[recipe for recipe in result.recipes if recipe.id.oid == oid],
        invalid_entries=result.invalid_entries,
    )


def load_recipe_from_repository_by_id(id_: str) -> RepositoryRecipesResult:
    parsed = parse_id(id_)
    if parsed is None:
        raise UserFacingError("Could not parse id")
    _, id_value = parsed
    return load_recipe_from_repository_by_oid(id_value)


def _repository_id_map() -> dict[object, RecipeInDb]:
    try:
        return list_repository_ids()
    except RepositoryServiceError as exc:
        raise _wrap_repository_error(exc) from exc


def _save_repository_entries(recipes: list[RecipeInDb]) -> int:
    try:
        return save_recipe_entries(recipes)
    except RepositoryServiceError as exc:
        raise _wrap_repository_error(exc) from exc


def _delete_repository_ids(ids: list[str]) -> tuple[list[str], list[str]]:
    try:
        return delete_recipe_ids(ids)
    except RepositoryServiceError as exc:
        raise _wrap_repository_error(exc) from exc


def get_today_recipes() -> list[RecipeInDb]:
    return KptnCookClient().list_today()


def save_todays_recipes() -> int:
    try:
        if not repository_needs_sync(date.today()):
            return 0
        recipes = get_today_recipes()
        return save_recipe_entries(recipes)
    except RepositoryServiceError as exc:
        raise _wrap_repository_error(exc) from exc


def get_mealie_client() -> MealieApiClient:
    settings = get_settings()
    client = MealieApiClient(str(settings.mealie_url))
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
    return load_kptncook_recipes_from_repository().recipes


def get_recipe_from_repository_by_oid(oid: str):
    return load_recipe_from_repository_by_oid(oid=oid).recipes


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
    settings = get_settings()
    if settings.kptncook_access_token is None:
        raise UserFacingError(
            f"Please set KPTNCOOK_ACCESS_TOKEN in your environment or {ENV_PATH}"
        )


def sync_with_mealie_result() -> SyncWithMealieResult:
    client = get_mealie_client()
    kptncook_recipes_from_mealie = get_kptncook_recipes_from_mealie(client)
    repository_result = load_kptncook_recipes_from_repository()
    ids_in_mealie = {r.extras.get("kptncook_id") for r in kptncook_recipes_from_mealie}
    created_count = 0
    failed_recipes: list[MealieSyncFailure] = []
    for recipe in repository_result.recipes:
        if recipe.id.oid in ids_in_mealie:
            continue
        recipe_name = localized_fallback(recipe.localized_title) or "Unknown title"
        try:
            client.create_recipe(kptncook_to_mealie(recipe))
        except Exception as exc:
            # Conversion, validation and serialization failures are local to this
            # recipe; keep processing the rest of the batch. Name collisions are
            # failures too, not evidence that this recipe's identity was imported.
            reason = _record_mealie_failure(recipe_name, exc).reason
        else:
            created_count += 1
            continue
        failure = MealieSyncFailure(recipe.id.oid, recipe_name, reason)
        failed_recipes.append(failure)
        logger.warning(
            "Failed to sync recipe %s (%s) with Mealie: %s",
            failure.recipe_name,
            failure.recipe_id,
            failure.reason,
        )
    return SyncWithMealieResult(
        created_count=created_count,
        invalid_repository_entries=repository_result.invalid_entries,
        failed=[
            MealieSyncIssue(failure.recipe_name, failure.reason)
            for failure in failed_recipes
        ],
        failed_recipes=failed_recipes,
    )


MEALIE_NAME_CLASH_REASON = (
    "Mealie already has a recipe with this name (another KptnCook recipe with "
    "the same title, your own recipe, or one left over from a failed sync); "
    "rename or delete it in Mealie and sync again to import this one"
)


def _describe_mealie_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return format_http_status_error(
            exc.response, action="syncing recipe with Mealie"
        )
    if isinstance(exc, httpx.HTTPError):
        return format_request_error(exc)
    return f"{type(exc).__name__}: {exc}"


def _record_mealie_failure(name: str, exc: Exception) -> MealieSyncIssue:
    reason = _describe_mealie_error(exc)
    if isinstance(exc, httpx.HTTPError) and not isinstance(exc, httpx.HTTPStatusError):
        # A write may have succeeded even if its response never arrived.
        reason = (
            f"{type(exc).__name__}: {reason}. "
            "The sync outcome is unknown; the next sync checks stored identity "
            "before creating recipes. No automatic retry or deletion was attempted."
        )
    logger.warning("Failed to create recipe %s in Mealie: %s", name, reason)
    return MealieSyncIssue(name=name, reason=reason)


def sync_with_mealie() -> int:
    result = sync_with_mealie_result()
    details = [
        f"{failure.recipe_name} ({failure.recipe_id}): {failure.reason}"
        for failure in result.failed_recipes
    ]
    if not details:
        details.extend(f"{issue.name}: {issue.reason}" for issue in result.failed)
    details.extend(f"{issue.name}: {issue.reason}" for issue in result.skipped_existing)
    if details:
        raise UserFacingError(
            f"Created {result.created_count} recipes. "
            f"Failed to sync {len(details)} recipes: {'; '.join(details)}"
        )
    return result.created_count


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

    saved_count = _save_repository_entries(recipes)
    return FavoritesBackupResult(
        favorite_count=len(favorites),
        saved_count=saved_count,
    )


def get_kptncook_access_token() -> str:
    settings = get_settings()
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
) -> DeleteSelectionResult:
    repository_result = load_kptncook_recipes_from_repository()
    recipes = repository_result.recipes
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

    existing_ids = {str(key) for key in _repository_id_map().keys()}
    missing_ids = [oid for oid in requested_ids if str(oid) not in existing_ids]
    to_delete_ids = [oid for oid in requested_ids if str(oid) in existing_ids]
    return DeleteSelectionResult(
        recipes=recipes,
        invalid_indices=invalid_indices,
        missing_ids=missing_ids,
        to_delete_ids=to_delete_ids,
        invalid_repository_entries=repository_result.invalid_entries,
    )


def delete_repository_recipes(ids: list[str]) -> tuple[list[str], list[str]]:
    return _delete_repository_ids(ids)


def search_recipe_by_id(id_: str) -> SearchResult:
    resolved_id = id_
    if resolved_id.startswith("https://share.kptncook.com/"):
        try:
            response = httpx.get(resolved_id, timeout=SHARE_URL_TIMEOUT)
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
    _save_repository_entries([recipe])
    return SearchResult(id_type=id_type, id_value=id_value, recipe=recipe)


def get_recipe_by_id(id_: str):
    found_recipes = load_recipe_from_repository_by_id(id_).recipes
    if len(found_recipes) == 0:
        raise UserFacingError("Recipe not found.")
    if len(found_recipes) > 1:
        raise UserFacingError("More than one recipe found with that ID.")
    return found_recipes


def export_recipes_to_paprika_result(recipe_id: str | None) -> PaprikaExportResult:
    repository_result = (
        load_recipe_from_repository_by_id(recipe_id)
        if recipe_id
        else load_kptncook_recipes_from_repository()
    )
    recipes = repository_result.recipes
    if recipe_id:
        if len(recipes) == 0:
            raise UserFacingError("Recipe not found.")
        if len(recipes) > 1:
            raise UserFacingError("More than one recipe found with that ID.")
    return PaprikaExportResult(
        filename=PaprikaExporter().export(recipes=recipes),
        invalid_repository_entries=repository_result.invalid_entries,
    )


def export_recipes_to_paprika(recipe_id: str | None) -> str:
    return export_recipes_to_paprika_result(recipe_id).filename


def export_recipes_to_tandoor_result(recipe_id: str | None) -> TandoorExportResult:
    repository_result = (
        load_recipe_from_repository_by_id(recipe_id)
        if recipe_id
        else load_kptncook_recipes_from_repository()
    )
    recipes = repository_result.recipes
    if recipe_id:
        if len(recipes) == 0:
            raise UserFacingError("Recipe not found.")
        if len(recipes) > 1:
            raise UserFacingError("More than one recipe found with that ID.")
    return TandoorExportResult(
        filenames=TandoorExporter().export(recipes=recipes),
        invalid_repository_entries=repository_result.invalid_entries,
    )


def export_recipes_to_tandoor(recipe_id: str | None) -> list[str]:
    return export_recipes_to_tandoor_result(recipe_id).filenames


def export_recipes_to_markdown_result(recipe_id: str | None) -> MarkdownExportResult:
    repository_result = (
        load_recipe_from_repository_by_id(recipe_id)
        if recipe_id
        else load_kptncook_recipes_from_repository()
    )
    recipes = repository_result.recipes
    if recipe_id:
        if len(recipes) == 0:
            raise UserFacingError("Recipe not found.")
        if len(recipes) > 1:
            raise UserFacingError("More than one recipe found with that ID.")
    return MarkdownExportResult(
        filenames=[str(path) for path in MarkdownExporter().export(recipes=recipes)],
        invalid_repository_entries=repository_result.invalid_entries,
    )


def export_recipes_to_markdown(recipe_id: str | None) -> list[str]:
    return export_recipes_to_markdown_result(recipe_id).filenames
