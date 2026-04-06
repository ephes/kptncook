from __future__ import annotations

from typing import Any

import httpx

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
        if path.startswith(("http://", "https://")):
            return path
        base = self.base_url.rstrip("/")
        if not path:
            return base
        if path.startswith("/"):
            return f"{base}{path}"
        return f"{base}/{path}"

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
