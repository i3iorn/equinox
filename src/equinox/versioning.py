"""Application version helpers shared across GUI and core modules."""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version


@lru_cache(maxsize=1)
def get_app_version() -> str:
    """Return the installed Equinox package version, or ``"dev"``."""
    try:
        return version("equinox")
    except PackageNotFoundError:
        return "dev"
