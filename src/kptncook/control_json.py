"""Strict decoding for JSON that controls Mealie follow-up requests."""

import json
import math
from functools import partial
from typing import Any

import httpx


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON number")
    return number


def decode_control_json(response: httpx.Response | bytes | bytearray) -> Any:
    decode = (
        response.json
        if isinstance(response, httpx.Response)
        else partial(json.loads, response)
    )
    try:
        return decode(
            object_pairs_hook=_unique_object,
            parse_constant=_finite_float,
            parse_float=_finite_float,
        )
    except (ValueError, RecursionError):
        # No member names, response fragments or tokens in the error chain.
        raise ValueError("Invalid Mealie control JSON") from None
