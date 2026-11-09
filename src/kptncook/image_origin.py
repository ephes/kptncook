"""Which image URLs may receive the KptnCook API key and be downloaded.

Recipe data names its image URLs, and exports attach the configured
``kptnkey`` before fetching them. A malformed or restored recipe must not be
able to send that key, or a download, to an unrelated or local host. Only
HTTPS URLs on an allowlisted host (default port, no user info) are admitted;
the same check applies to every redirect target.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

# KptnCook serves recipe images from its CloudFront distribution and from
# images.kptncook.com.
DEFAULT_IMAGE_HOSTS: tuple[str, ...] = (
    "d2am1qai33sroc.cloudfront.net",
    "images.kptncook.com",
)

_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_UNSAFE_URL_CHARS = re.compile(r"[\x00-\x20\x7f\\]")


class ImageOriginError(ValueError):
    """An image URL is not on an allowed origin (the message never names it)."""


def parse_image_hosts(value: str | None) -> tuple[str, ...]:
    """Validate a comma-separated list of extra image host names."""
    if value is None or not value.strip():
        return ()
    hosts: list[str] = []
    for raw in value.split(","):
        host = raw.strip().lower()
        if not host:
            continue
        labels = host.split(".")
        if (
            len(host) > 253
            or len(labels) < 2
            or not all(_HOST_LABEL.match(label) for label in labels)
            or labels[-1].isdigit()  # IPv4 literals are not image CDNs
            or _is_numeric_address(host)
        ):
            raise ValueError(
                "KPTNCOOK_IMAGE_HOSTS must list host names such as "
                "images.example.com (no scheme, port, path or IP address)"
            )
        hosts.append(host)
    return tuple(hosts)


def _is_numeric_address(host: str) -> bool:
    """True for any spelling the resolver reads as an IP address.

    ``inet_aton`` accepts the legacy forms (``127.0.0.0x1``, ``0177.1``,
    ``2130706433``) that ``getaddrinfo`` resolves numerically.
    """
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    try:
        socket.inet_aton(host)
    except OSError:
        return False
    return True


def allowed_image_hosts() -> frozenset[str]:
    from .config import get_settings

    extra = parse_image_hosts(get_settings().kptncook_image_hosts)
    return frozenset(DEFAULT_IMAGE_HOSTS + extra)


def is_allowed_image_url(url: str, hosts: frozenset[str] | None = None) -> bool:
    if not isinstance(url, str) or _UNSAFE_URL_CHARS.search(url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme.lower() != "https" or "@" in parts.netloc:
        return False
    if port not in (None, 443):
        return False
    host = parts.hostname
    if not host:
        return False
    return host in (allowed_image_hosts() if hosts is None else hosts)


def check_image_url(url: str) -> None:
    if not is_allowed_image_url(url):
        raise ImageOriginError("Image URL is not on an allowed image host")
