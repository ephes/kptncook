"""Bounded downloads for optional recipe images."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx

MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_DOWNLOAD_DEADLINE = 60.0
IMAGE_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


async def _download_image(url: str, follow_redirects: bool) -> bytes:
    async with httpx.AsyncClient(
        timeout=IMAGE_DOWNLOAD_TIMEOUT,
        follow_redirects=follow_redirects,
        headers={"Accept-Encoding": "identity"},
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise httpx.RequestError(
                    "Image response uses unsupported content encoding"
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_IMAGE_BYTES:
                    raise httpx.RequestError("Image exceeds download size limit")
                body.extend(chunk)
            return bytes(body)


async def _download_with_deadline(url: str, follow_redirects: bool) -> bytes:
    try:
        return await asyncio.wait_for(
            _download_image(url, follow_redirects), timeout=IMAGE_DOWNLOAD_DEADLINE
        )
    except asyncio.TimeoutError:
        raise httpx.ReadTimeout("Image exceeded total download time limit") from None


def download_image(url: str, *, follow_redirects: bool = False) -> bytes:
    """Fetch at most 10 MiB, with a 60-second deadline including redirects."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_download_with_deadline(url, follow_redirects))
    # Notebook kernels and other async hosts already own this thread's loop.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(
            download_image, url, follow_redirects=follow_redirects
        ).result()
