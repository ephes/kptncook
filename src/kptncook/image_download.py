"""Bounded downloads for optional recipe images."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx

from .image_origin import is_allowed_image_url
from .exporter_utils import ExportBudget

MAX_IMAGE_REDIRECTS = 5
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_DOWNLOAD_DEADLINE = 60.0
IMAGE_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


async def _download_image(
    url: str, follow_redirects: bool, budget: ExportBudget | None = None
) -> bytes:
    _check_origin(url)
    if budget:
        budget.check_asset_capacity()
    # Redirects are followed by hand so every target passes the origin check.
    async with httpx.AsyncClient(
        timeout=IMAGE_DOWNLOAD_TIMEOUT,
        follow_redirects=False,
        trust_env=False,
        headers={"Accept-Encoding": "identity"},
    ) as client:
        for _ in range(MAX_IMAGE_REDIRECTS + 1):
            async with client.stream("GET", url) as response:
                if follow_redirects and response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise httpx.RequestError("Image redirect has no target")
                    url = str(response.url.join(location))
                    _check_origin(url)
                    continue
                response.raise_for_status()
                if (
                    response.status_code != httpx.codes.OK
                    or "content-range" in response.headers
                ):
                    raise httpx.RequestError(
                        "Image response is not a complete representation"
                    )
                if (
                    response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    raise httpx.RequestError(
                        "Image response uses unsupported content encoding"
                    )
                declared_length = _declared_length(response)
                if budget is not None and declared_length is not None:
                    budget.check_asset_capacity(declared_length)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if budget:
                        budget.charge_asset(len(chunk))
                    if len(body) + len(chunk) > MAX_IMAGE_BYTES:
                        raise httpx.RequestError("Image exceeds download size limit")
                    body.extend(chunk)
                if not body or (
                    declared_length is not None and len(body) != declared_length
                ):
                    raise httpx.RequestError(
                        "Image response is not a complete representation"
                    )
                return bytes(body)
        raise httpx.RequestError("Image exceeded redirect limit")


def _declared_length(response: httpx.Response) -> int | None:
    header = response.headers.get("content-length")
    if header is None:
        return None
    value = header.strip(" \t")
    if not value.isascii() or not value.isdecimal():
        raise httpx.RequestError("Image response has an invalid Content-Length")
    digits = value.lstrip("0") or "0"
    # Bound the decimal conversion even for an unusually long header.
    max_digits = str(MAX_IMAGE_BYTES)
    if len(digits) > len(max_digits) or (
        len(digits) == len(max_digits) and digits > max_digits
    ):
        raise httpx.RequestError("Image exceeds download size limit")
    return int(digits)


def _check_origin(url: str) -> None:
    if not is_allowed_image_url(url):
        # Never name the URL: it may carry the API key.
        raise httpx.RequestError("Image URL is not on an allowed image host")


async def _download_with_deadline(
    url: str, follow_redirects: bool, budget: ExportBudget | None = None
) -> bytes:
    try:
        return await asyncio.wait_for(
            _download_image(url, follow_redirects, budget),
            timeout=IMAGE_DOWNLOAD_DEADLINE,
        )
    except asyncio.TimeoutError:
        raise httpx.ReadTimeout("Image exceeded total download time limit") from None


def download_image(
    url: str, *, follow_redirects: bool = False, budget: ExportBudget | None = None
) -> bytes:
    """Fetch at most 10 MiB, with a 60-second deadline including redirects.

    The URL and every redirect target must be on an allowed image host
    (see ``image_origin``); otherwise ``httpx.RequestError`` is raised before
    any request to that target. Only HTTP 200 responses without Content-Range
    are read. The body must be nonempty and match Content-Length if present.
    The image client connects directly and ignores HTTPX proxy and CA settings
    from the environment. An optional export budget charges every streamed
    chunk, including chunks from failed images. An oversized declaration is
    refused before reading the body. Exhaustion stops the batch.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_download_with_deadline(url, follow_redirects, budget))
    # Notebook kernels and other async hosts already own this thread's loop.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(
            download_image, url, follow_redirects=follow_redirects, budget=budget
        ).result()
