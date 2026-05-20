"""System-level default request headers.

The implementation lives in ``equinox.application.requests._assembly``
so that the application service layer can import it without triggering the
GUI package chain (which would cause a circular import).

This module re-exports ``apply_default_headers`` for backward compatibility
with any GUI code that imports it directly from this location.
"""

from equinox.application.requests._assembly import (  # noqa: F401
    apply_default_headers,
)

__all__ = ["apply_default_headers"]
