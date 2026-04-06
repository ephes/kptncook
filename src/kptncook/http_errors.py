from __future__ import annotations

import httpx


class UserFacingError(Exception):
    """Raised when a service wants the CLI to print a user-facing message."""


def _json_body(response: httpx.Response) -> object | None:
    if "application/json" not in response.headers.get("content-type", ""):
        return None
    try:
        return response.json()
    except ValueError:
        return None


def extract_mealie_detail_message(response: httpx.Response) -> str | None:
    response_data = _json_body(response)
    if isinstance(response_data, dict):
        detail = response_data.get("detail", {})
        if isinstance(detail, dict):
            detail_message = detail.get("message")
            if isinstance(detail_message, str):
                return detail_message
    return None


def extract_http_error_message(response: httpx.Response) -> str | None:
    response_data = _json_body(response)
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


def format_http_status_error(
    response: httpx.Response,
    *,
    action: str,
    unavailable_on_redirect: bool = False,
) -> str:
    message = f"HTTP {response.status_code} while {action}"
    if unavailable_on_redirect and 300 <= response.status_code < 400:
        message = f"{message} (endpoint may no longer be available)"
    detail = extract_http_error_message(response)
    if detail:
        message = f"{message}: {detail}"
    return message


def format_request_error(exc: httpx.HTTPError) -> str:
    return f"Request failed: {exc}"
