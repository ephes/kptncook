"""Keep API keys, tokens and credentials out of httpx and httpcore log records.

httpx logs every request at INFO as ``HTTP Request: GET <url> "HTTP/1.1 200 OK"``,
and the KptnCook API and its image URLs carry the ``kptnkey`` in the query.
httpcore's DEBUG trace includes header tuples. Nothing here is logged unless the
caller configures logging, but once someone does, the records must not contain
secrets. A filter on each of these loggers rewrites the formatted message before
any handler sees it.
"""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"

# Query parameters whose value is a secret (matched case-insensitively).
SENSITIVE_QUERY_PARAMS = (
    "kptnkey",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "key",
    "password",
    "secret",
    "client_secret",
    "signature",
    "sig",
)

# Header names whose value is a secret (matched case-insensitively).
SENSITIVE_HEADERS = (
    "authorization",
    "proxy-authorization",
    "token",
    "kptnkey",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
)

# httpx and httpcore log on these names; a filter on a parent logger does not
# see records of its children, so each one gets its own.
HTTP_LOGGERS = (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)

_QUERY = re.compile(
    # The value runs to the next "&", "#", whitespace, double quote or angle
    # bracket: "'" and ";" may stay unencoded in a URL, so they do not end it.
    r"(?P<prefix>[?&;](?:%s)=)[^&#\s\"<>]*"
    % "|".join(re.escape(name) for name in SENSITIVE_QUERY_PARAMS),
    re.IGNORECASE,
)
_USERINFO = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^/?#@\s'\"]+@", re.IGNORECASE
)
_HEADER_NAMES = "|".join(re.escape(name) for name in SENSITIVE_HEADERS)
# httpcore's trace: (b'Authorization', b'Bearer ...') or ('token', '...').
_HEADER_TUPLE = re.compile(
    r"(?P<name>\(b?(?P<q>['\"])(?:%s)(?P=q),\s*b?)(?P<vq>['\"])(?:\\.|(?!(?P=vq)).)*(?P=vq)"
    % _HEADER_NAMES,
    re.IGNORECASE,
)
# A mapping repr: {'Authorization': 'Bearer ...'} or Headers({...}).
_HEADER_ITEM = re.compile(
    r"(?P<name>(?P<q>['\"])(?:%s)(?P=q):\s*)(?P<vq>['\"])(?:\\.|(?!(?P=vq)).)*(?P=vq)"
    % _HEADER_NAMES,
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """``text`` with secret query values, URL credentials and header values replaced."""

    text = _QUERY.sub(lambda m: m.group("prefix") + REDACTED, text)
    text = _USERINFO.sub(lambda m: m.group("scheme") + REDACTED + "@", text)
    text = _HEADER_TUPLE.sub(
        lambda m: m.group("name") + m.group("vq") + REDACTED + m.group("vq"), text
    )
    text = _HEADER_ITEM.sub(
        lambda m: m.group("name") + m.group("vq") + REDACTED + m.group("vq"), text
    )
    return text


class SecretRedactingFilter(logging.Filter):
    """Rewrite a record's message so no handler can format a secret into it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # A record that cannot be formatted is not ours to fix; let the
            # handler report it as it would without this filter.
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


_FILTER = SecretRedactingFilter()


def install() -> None:
    """Attach the filter to the httpx and httpcore loggers (idempotent)."""

    for name in HTTP_LOGGERS:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
