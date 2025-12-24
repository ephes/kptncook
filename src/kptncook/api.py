import re
from collections.abc import Sequence
from datetime import date
from time import time
from typing import Any, Literal
from urllib.parse import urljoin

import httpx

from .config import settings
from .repositories import RecipeInDb

RecipeIdentifier = tuple[Literal["oid", "uid"], str]


def ids_to_payload(ids: list[RecipeIdentifier]) -> list[dict]:
    """
    Convert a list of (type, id) tuples to a list of dicts that
    can be used as payload for the kptncook api.
    """
    payload = []
    for id_type, id_value in ids:
        if id_type == "oid":
            payload.append({"identifier": id_value})
        elif id_type == "uid":
            payload.append({"uid": id_value})
    return payload


def _coerce_recipe_identifier(value: object) -> RecipeIdentifier | None:
    if isinstance(value, tuple) and len(value) == 2:
        id_type, id_value = value
        if id_type in ("oid", "uid") and id_value is not None:
            return id_type, str(id_value)
    if isinstance(value, dict):
        identifier = value.get("identifier")
        if identifier is not None:
            nested = _coerce_recipe_identifier(identifier)
            if nested is not None:
                return nested if nested[0] == "oid" else ("oid", nested[1])
            if not isinstance(identifier, dict):
                return "oid", str(identifier)
        uid = value.get("uid")
        if uid is not None:
            nested = _coerce_recipe_identifier(uid)
            if nested is not None:
                return nested if nested[0] == "uid" else ("uid", nested[1])
            if not isinstance(uid, dict):
                return "uid", str(uid)
        for key in ("_id", "id", "recipeId", "recipe_id"):
            if key in value:
                nested = _coerce_recipe_identifier(value.get(key))
                if nested is not None:
                    return nested
        for key in ("$oid", "oid"):
            if key in value:
                nested = _coerce_recipe_identifier(value.get(key))
                if nested is not None:
                    return nested if nested[0] == "oid" else ("oid", nested[1])
        for key in ("recipe", "recipeSummary", "recipe_summary", "summary"):
            if key in value:
                nested = _coerce_recipe_identifier(value.get(key))
                if nested is not None:
                    return nested
    if isinstance(value, str):
        parsed = parse_id(value)
        if parsed is not None:
            return parsed
        if looks_like_uid(value):
            return "uid", value
        if looks_like_oid(value):
            return "oid", value
    return None


def _collect_recipe_identifiers(items: Sequence[object]) -> list[RecipeIdentifier]:
    identifiers: list[RecipeIdentifier] = []
    seen: set[RecipeIdentifier] = set()
    for item in items:
        parsed = _coerce_recipe_identifier(item)
        if parsed is None or parsed in seen:
            continue
        identifiers.append(parsed)
        seen.add(parsed)
    return identifiers


def _extract_dailies_payload(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("recipes", "dailies", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_discovery_list_payload(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("recipes", "items", "list", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        for value in payload.values():
            if isinstance(value, dict):
                nested = _extract_discovery_list_payload(value)
                if nested:
                    return nested
    return []


class KptnCookClient:
    """
    Client for the kptncook api.
    """

    def __init__(
        self, base_url=settings.kptncook_api_url, api_key=settings.kptncook_api_key
    ):
        self.base_url = str(base_url)
        self.headers = {
            "content-type": "application/json",
            "Accept": "application/vnd.kptncook.mobile-v8+json",
            "User-Agent": "Platform/Android/12.0.1 App/7.10.1",
            "hasIngredients": "yes",
        }
        self.api_key = api_key
        if settings.kptncook_access_token is not None:
            self.headers["Token"] = settings.kptncook_access_token

    @property
    def logged_in(self):
        return "Token" in self.headers

    def to_url(self, path):
        return urljoin(self.base_url, path)

    def __getattr__(self, name):
        """
        Return proxy for httpx, joining base_url with path and
        providing authentication headers automatically.
        """

        def proxy(path, **kwargs):
            url = self.to_url(path)
            set_headers = kwargs.get("headers", {})
            kwargs["headers"] = set_headers | self.headers
            return getattr(httpx, name)(url, **kwargs)

        return proxy

    def _standard_query_params(
        self,
        *,
        lang: str | None = None,
        store: str | None = None,
        preferences: str | None = None,
    ) -> dict[str, str]:
        if lang is None:
            lang = settings.kptncook_lang
        if store is None:
            store = settings.kptncook_store
        if preferences is None:
            preferences = settings.kptncook_preferences
        params = {"kptnkey": str(self.api_key)}
        if lang is not None:
            params["lang"] = str(lang)
        if store is not None:
            params["store"] = str(store)
        if preferences is not None:
            params["preferences"] = str(preferences)
        return params

    def list_today(self) -> list[RecipeInDb]:
        """
        Get all recipes for today from kptncook api.
        """
        time_str = str(time())
        response = self.get(f"recipes/de/{time_str}?kptnkey={self.api_key}")
        response.raise_for_status()
        recipes = []
        today = date.today()
        for data in response.json():
            recipes.append(RecipeInDb(date=today, data=data))
        return recipes

    def list_dailies(
        self,
        *,
        recipe_filter: str | None = None,
        zone: str | None = None,
        is_subscribed: bool | None = None,
        lang: str | None = None,
        store: str | None = None,
        preferences: str | None = None,
    ) -> list[RecipeInDb]:
        """
        Get daily recipes from kptncook api.
        """
        params = self._standard_query_params(
            lang=lang, store=store, preferences=preferences
        )
        if recipe_filter is not None:
            params["recipeFilter"] = recipe_filter
        if zone is not None:
            params["zone"] = zone
        if is_subscribed is not None:
            params["isSubscribed"] = "true" if is_subscribed else "false"
        response = self.get("/dailies", params=params)
        response.raise_for_status()
        payload = _extract_dailies_payload(response.json())
        today = date.today()
        return [RecipeInDb(date=today, data=data) for data in payload]

    def get_access_token(self, username: str, password: str) -> str:
        """
        Get access token for kptncook api.
        """
        headers = self.headers.copy()
        headers["kptnkey"] = str(self.api_key)
        response = self.post(
            "/auth/login",
            json={"email": username, "password": password},
            headers=headers,
        )
        response.raise_for_status()
        token_data = response.json()
        return token_data["accessToken"]

    def list_favorites(self) -> list[str]:
        """
        Get a list of favorite recipes.
        """
        response = self.get("/favorites")
        response.raise_for_status()
        return response.json()["favorites"]

    def get_by_ids(self, ids: list[RecipeIdentifier]) -> list[RecipeInDb]:
        """
        Get recipes from a list of ids.
        """
        return self.resolve_recipe_summaries(ids)

    def resolve_recipe_summaries(self, items: Sequence[object]) -> list[RecipeInDb]:
        """
        Resolve recipe summary payloads or identifiers into full recipes.
        """
        identifiers = _collect_recipe_identifiers(items)
        if not identifiers:
            return []
        payload = ids_to_payload(identifiers)
        if not payload:
            return []
        # timeout disabled because saving more than 999 favorites didn't work for @brotkrume
        response = self.post(
            f"/recipes/search?kptnkey={self.api_key}", json=payload, timeout=None
        )
        response.raise_for_status()
        results = response.json()
        if results is None:
            results = []
        return [RecipeInDb(date=date.today(), data=data) for data in results]

    def get_discovery_screen(
        self,
        *,
        lang: str | None = None,
        store: str | None = None,
        preferences: str | None = None,
        version: int = 2,
    ) -> dict[str, Any] | list[Any]:
        """
        Get discovery screen payload from kptncook api.
        """
        params = self._standard_query_params(
            lang=lang, store=store, preferences=preferences
        )
        params["v"] = str(version)
        response = self.get("discovery/screen", params=params)
        response.raise_for_status()
        return response.json()

    def get_discovery_list(
        self,
        *,
        list_type: str,
        list_id: str | None = None,
        lang: str | None = None,
        store: str | None = None,
        preferences: str | None = None,
    ) -> list[object]:
        """
        Get discovery list summary entries from kptncook api.
        """
        params = self._standard_query_params(
            lang=lang, store=store, preferences=preferences
        )
        if list_type in ("latest", "recommended"):
            path = f"discovery/list/{list_type}"
        elif list_type in ("curated", "automated"):
            if list_id is None:
                raise ValueError("list_id is required for curated or automated lists")
            path = f"discovery/list/{list_type}/{list_id}"
        else:
            path = f"discovery/list/{list_type}"
        response = self.get(path, params=params)
        response.raise_for_status()
        return _extract_discovery_list_payload(response.json())

    def get_onboarding_recipes(
        self,
        *,
        tags: list[str],
        lang: str | None = None,
        store: str | None = None,
        preferences: str | None = None,
    ) -> list[object]:
        """
        Get onboarding recipe summary entries from kptncook api.
        """
        if not tags:
            return []
        params = self._standard_query_params(
            lang=lang, store=store, preferences=preferences
        )
        response = self.post(
            "recipes/onboarding",
            params=params,
            json={"tags": tags},
        )
        response.raise_for_status()
        return _extract_discovery_list_payload(response.json())

    def list_popular_ingredients(
        self,
        *,
        lang: str | None = None,
        store: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get popular ingredients from kptncook api.
        """
        if not self.logged_in:
            raise RuntimeError("Token required for /ingredients/popular")
        params = self._standard_query_params(lang=lang, store=store)
        response = self.get("ingredients/popular", params=params)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("ingredients", "items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def get_recipes_with_ingredients(
        self,
        *,
        ingredient_ids: list[str],
        lang: str | None = None,
        store: str | None = None,
        preferences: str | None = None,
    ) -> list[object]:
        """
        Get recipe summaries that match ingredient ids.
        """
        if not ingredient_ids:
            return []
        params = self._standard_query_params(
            lang=lang, store=store, preferences=preferences
        )
        response = self.post(
            "recipes/withIngredients",
            params=params,
            json={"ingredientIds": ingredient_ids},
        )
        response.raise_for_status()
        return _extract_discovery_list_payload(response.json())


def looks_like_uid(token: str) -> bool:
    correct_len = len(token) == 8 or len(token) == 7
    is_alnum = token.isalnum()
    return correct_len and is_alnum


def looks_like_oid(token: str) -> bool:
    correct_len = len(token) == 24
    is_hex = token.isalnum()
    return correct_len and is_hex


def parse_id(text: str) -> tuple[Literal["oid", "uid"], str] | None:
    """
    Parse recipe id (uid or uid) from url/text.
    """
    try:
        uid = next(part for part in re.split(r"/|\?", text) if looks_like_uid(part))
        return "uid", uid
    except StopIteration:
        pass
    try:
        oid = next(part for part in re.split(r" |,|/", text) if looks_like_oid(part))
        return "oid", oid
    except StopIteration:
        pass
    return None
