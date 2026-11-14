"""Strict member admission for JSON that controls Mealie follow-up requests."""

from typing import Any

import httpx


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def decode_control_json(response: httpx.Response) -> Any:
    try:
        return response.json(object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        # No member names, response fragments or tokens in the error chain.
        raise ValueError("Invalid Mealie control JSON") from None
