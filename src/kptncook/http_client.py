from __future__ import annotations

from typing import Any

import httpx

from . import log_redaction

# httpx logs request URLs, which carry the kptnkey; keep secrets out of logs.
log_redaction.install()

DEFAULT_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class BaseHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | float = DEFAULT_REQUEST_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = str(base_url)
        self.headers = dict(headers or {})
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BaseHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def to_url(self, path: str) -> str:
        try:
            base = httpx.URL(self.base_url)
            candidate = httpx.URL(path)
            if not candidate.is_absolute_url:
                if candidate.scheme or candidate.host:
                    raise ValueError
                prefix = self.base_url.rstrip("/")
                candidate = httpx.URL(f"{prefix}/{path.lstrip('/')}") if path else base
            if (
                base.scheme not in ("http", "https")
                or not base.host
                or base.userinfo
                or candidate.userinfo
                or (candidate.scheme, candidate.host, candidate.port)
                != (base.scheme, base.host, base.port)
            ):
                raise ValueError
        except (httpx.InvalidURL, ValueError):
            raise ValueError(
                "API request must use the configured origin without userinfo"
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
        merged_headers = self.headers | (headers or {})
        request_kwargs = dict(kwargs)
        if request_kwargs.get("follow_redirects"):
            raise ValueError("API clients do not support automatic redirects")
        # An injected pool may enable redirects; Token is not stripped by HTTPX
        # on a cross-origin redirect as Authorization is.
        request_kwargs["follow_redirects"] = False
        request_kwargs["headers"] = merged_headers
        if timeout is not httpx.USE_CLIENT_DEFAULT:
            request_kwargs["timeout"] = timeout
        return self._client.request(method, self.to_url(path), **request_kwargs)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)
