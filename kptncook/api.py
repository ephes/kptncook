import re
from datetime import date
from time import time
from typing import Literal

import httpx

from .config import settings
from .repositories import RecipeInDb


def ids_to_payload(ids: list[tuple[str, str]]) -> list[dict]:
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


class KptnCookClient:
    """
    Client for the kptncook api.
    """

    def __init__(
        self, base_url=settings.kptncook_api_url, api_key=settings.kptncook_api_key
    ):
        self.base_url = base_url
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
        return f"{self.base_url}{path}"

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

    def list_today(self) -> list[RecipeInDb]:
        """
        Get all recipes for today from kptncook api.
        """
        time_str = str(time())
        response = self.get(f"/recipes/de/{time_str}?kptnkey={self.api_key}")
        response.raise_for_status()
        recipes = []
        today = date.today()
        for data in response.json():
            recipes.append(RecipeInDb(date=today, data=data))
        return recipes

    def get_access_token(self, username: str, password: str) -> str:
        """
        Get access token for kptncook api.
        """
        response = self.post(
            "/login/userpass",
            json={"email": username, "password": password},
        )
        response.raise_for_status()
        token_data = response.json()
        return token_data["accessToken"]

    def list_favorites(self) -> list[str]:
        """
        Get list of favorite recipes.
        """
        response = self.get("/favorites")
        response.raise_for_status()
        return response.json()["favorites"]

    def get_by_ids(self, ids: list[tuple[str, str]]) -> list[RecipeInDb]:
        """
        Get recipes from list of ids.
        """
        payload = ids_to_payload(ids)
        response = self.post(f"/recipes/search?kptnkey={self.api_key}", json=payload)
        response.raise_for_status()
        results = response.json()
        if results is None:
            results = []
        results = [RecipeInDb(date=date.today(), data=data) for data in results]
        return results


def looks_like_uid(token: str) -> bool:
    correct_len = len(token) == 8
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
