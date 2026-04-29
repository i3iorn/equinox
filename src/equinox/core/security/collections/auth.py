"""Security-enabled Collection auth surface (wrapper).

This module provides a thin wrapper around the existing CollectionAuthMixin
to keep imports consistent while the underlying implementation can evolve
under the security namespace.
"""

from __future__ import annotations

from equinox.storage.collections.auth import CollectionAuthMixin  # type: ignore

__all__ = ["CollectionAuthMixin"]
