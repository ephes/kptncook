from __future__ import annotations

import logging
from contextlib import suppress
from enum import Enum
from typing import Any

import httpx

from . import log_redaction

# httpx logs request URLs, which carry the kptnkey; keep secrets out of logs.
log_redaction.install()
logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class _TimeoutDefault(Enum):
    UNSPECIFIED = "unspecified"


class BaseHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout
        | float
        | None
        | _TimeoutDefault = _TimeoutDefault.UNSPECIFIED,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = str(base_url)
        self.headers = dict(headers or {})
        self._timeout = timeout
        self._client = (
            client
            if client is not None
            else httpx.Client(
                timeout=DEFAULT_REQUEST_TIMEOUT
                if timeout is _TimeoutDefault.UNSPECIFIED
                else timeout
            )
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BaseHttpClient:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, *_args: object) -> None:
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise
            with suppress(Exception):
                logger.warning("HTTP client cleanup failed.")

    def to_url(self, path: str) -> str:
        try:
            base = httpx.URL(self.base_url)
            if (
                base.scheme not in ("http", "https")
                or not base.host
                or base.userinfo
                or base != base.copy_with(query=None, fragment=None)
            ):
                raise ValueError
            candidate = httpx.URL(path)
            if not path:
                candidate = base
            elif not candidate.is_absolute_url:
                if candidate.scheme or candidate.host:
                    raise ValueError
                # The admitted base has no query, so raw_path is its encoded path.
                prefix = base.raw_path.decode("ascii").rstrip("/")
                origin = f"{base.scheme}://{base.netloc.decode('ascii')}"
                candidate = httpx.URL(f"{origin}{prefix}/{path.lstrip('/')}")
            if candidate.userinfo or (
                candidate.scheme,
                candidate.host,
                candidate.port,
            ) != (base.scheme, base.host, base.port):
                raise ValueError
        except (httpx.InvalidURL, ValueError):
            raise ValueError(
                "API request must use the configured origin without userinfo. "
                "The base URL must have no query or fragment."
            ) from None
        return str(candidate)

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | float | object = httpx.USE_CLIENT_DEFAULT,
        **kwargs: Any,
    ) -> httpx.Response:
        request_kwargs = dict(kwargs)
        if request_kwargs.get("follow_redirects"):
            raise ValueError("API clients do not support automatic redirects")
        # An injected pool may enable redirects; Token is not stripped by HTTPX
        # on a cross-origin redirect as Authorization is.
        request_kwargs["follow_redirects"] = False
        url = self.to_url(path)
        merged_headers = httpx.Headers(self.headers | (headers or {}))
        # Replace Host case-insensitively and override any injected pool default.
        merged_headers["Host"] = httpx.URL(url).netloc.decode("ascii")
        request_kwargs["headers"] = merged_headers
        if timeout is not httpx.USE_CLIENT_DEFAULT:
            request_kwargs["timeout"] = timeout
        elif self._timeout is not _TimeoutDefault.UNSPECIFIED:
            request_kwargs["timeout"] = self._timeout
        return self._client.request(method, url, **request_kwargs)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)
