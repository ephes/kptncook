"""
Standalone ``kptncook-setup`` entry point.

The implementation lives in :mod:`kptncook.setup`; this module only re-exports it
so the console script keeps working while the same command is also available as
``kptncook setup``. Keeping the implementation in the ``kptncook`` package avoids
the import cycle that would arise if the main CLI imported ``kptncook_setup``.
"""

from __future__ import annotations

from kptncook.setup import DEFAULT_API_URL, _fetch_access_token, cli, setup
from kptncook.env import ENV_PATH, ENV_TEMPLATE

__all__ = [
    "DEFAULT_API_URL",
    "ENV_PATH",
    "ENV_TEMPLATE",
    "_fetch_access_token",
    "cli",
    "setup",
]
