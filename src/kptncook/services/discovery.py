from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any


@dataclass(frozen=True)
class DiscoveryListSummary:
    list_id: str | None
    title: str | None
    list_type: str | None


@dataclass(frozen=True)
class DiscoveryScreenData:
    lists: list[DiscoveryListSummary]
    quick_search: list[str]


MAX_SCREEN_DEPTH = 16
MAX_SCREEN_WORK = 4096


class DiscoveryScreenError(ValueError):
    """A malformed screen must not produce a partial presentation."""


def _extract_nested_list(payload: object, keys: tuple[str, ...]) -> list[Any]:
    # Incremental DFS preserves supported-key and dictionary insertion order.
    # Lists are leaves. Empty lists at nested nodes stop that node's search,
    # but do not hide a later sibling's nonempty result.
    stack: list[tuple[Iterator[object], int, frozenset[int]]] = [
        (iter((payload,)), 0, frozenset())
    ]
    work = 0

    def charge() -> None:
        nonlocal work
        work += 1
        if work > MAX_SCREEN_WORK:
            raise DiscoveryScreenError(
                "Malformed discovery screen: work limit exceeded"
            )

    while stack:
        children, depth, ancestors = stack[-1]
        try:
            node = next(children)
        except StopIteration:
            stack.pop()
            continue
        charge()
        if not isinstance(node, dict):
            continue
        if depth >= MAX_SCREEN_DEPTH or id(node) in ancestors:
            raise DiscoveryScreenError(
                "Malformed discovery screen: cycle or depth limit exceeded"
            )
        for key in keys:
            charge()
            value = node.get(key)
            if isinstance(value, list):
                if value or depth == 0:
                    return value
                break
        else:
            stack.append((iter(node.values()), depth + 1, ancestors | {id(node)}))
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
DISCOVERY_LIST_TYPES_REQUIRE_ID = {"curated", "automated"}


def normalize_discovery_list_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _DISCOVERY_LIST_TYPES:
        raise ValueError(
            "list-type must be one of: latest, recommended, curated, automated"
        )
    return normalized


def normalize_discovery_list_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if (
        normalized in {".", ".."}
        or any(not (char.isalnum() or char in "-._~") for char in normalized)
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(
            "list-id must be one opaque path component: letters, numbers, -._~"
        )
    return normalized


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


def normalize_tags(tags: list[str]) -> list[str]:
    return _normalize_list_input(tags)


def normalize_ingredient_ids(ingredient_ids: list[str]) -> list[str]:
    return _normalize_list_input(ingredient_ids)


def parse_discovery_screen(payload: object) -> DiscoveryScreenData:
    list_summaries: list[DiscoveryListSummary] = []
    for entry in _extract_discovery_list_entries(payload):
        list_id = _extract_discovery_list_id(entry)
        title = _extract_discovery_list_title(entry)
        list_type = _extract_discovery_list_type(entry)
        if list_id is None and title is None and list_type is None:
            continue
        list_summaries.append(
            DiscoveryListSummary(
                list_id=list_id,
                title=title,
                list_type=list_type,
            )
        )

    quick_search: list[str] = []
    for entry in _extract_quick_search_entries(payload):
        label = _format_quick_search_entry(entry)
        if label is not None:
            quick_search.append(label)

    return DiscoveryScreenData(lists=list_summaries, quick_search=quick_search)
