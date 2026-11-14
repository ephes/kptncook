from __future__ import annotations

import httpx

from .control_json import decode_control_json


MAX_ERROR_BODY_BYTES = 16 * 1024
# Only known protocol details are safe to display; arbitrary body/URL text may
# contain credentials even when it looks like an ordinary error description.
PUBLIC_ERROR_MESSAGES = frozenset(
    {
        "missing_kptnkey",
        "missing_token",
        "invalid_token",
        "invalid_credentials",
        "Invalid credentials",
        "Recipe already exists",
        "This item already exists.",
    }
)


class UserFacingError(Exception):
    """Raised when a service wants the CLI to print a user-facing message."""


def _json_body(response: httpx.Response) -> object | None:
    media_type = (
        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if media_type != "application/json" and not (
        media_type.startswith("application/") and media_type.endswith("+json")
    ):
        return None
    try:
        if len(response.content) > MAX_ERROR_BODY_BYTES:
            return None
        return decode_control_json(response)
    except (ValueError, RecursionError, httpx.ResponseNotRead):
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
            if isinstance(value, str) and value in PUBLIC_ERROR_MESSAGES:
                return value
        detail = response_data.get("detail")
        if isinstance(detail, dict):
            detail_message = detail.get("message")
            if (
                isinstance(detail_message, str)
                and detail_message in PUBLIC_ERROR_MESSAGES
            ):
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
    if isinstance(exc, httpx.TimeoutException):
        category = "timeout"
    elif isinstance(exc, httpx.ConnectError):
        category = "connection error"
    elif isinstance(exc, httpx.ProtocolError):
        category = "protocol error"
    elif isinstance(exc, httpx.NetworkError):
        category = "network error"
    else:
        category = "HTTP error"
    return f"Request failed: {category}"
