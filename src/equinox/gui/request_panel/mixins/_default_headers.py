"""System-level default request headers.

Headers defined here are injected only when the user has **not** already
provided a header with the same name (case-sensitive key comparison).

Each entry maps a header name to either:
- a ``str`` — a static default value, or
- a ``Callable[[], str]`` — a zero-argument factory invoked fresh per request
  to generate a unique/dynamic value.

To add a new system default, append an entry to ``_SYSTEM_DEFAULTS``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Union
from uuid import uuid4

from equinox.versioning import get_app_version

if TYPE_CHECKING:
    from equinox.core.request import Request

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_SYSTEM_DEFAULTS: Dict[str, Union[str, Callable[[], str]]] = {
    # Unique identifier for each outbound request – useful for distributed
    # tracing and server-side correlation logs.
    "X-Request-ID": lambda: str(uuid4()),
    "User-Agent": "Equinox API testing v" + get_app_version(),
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
}


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------


def apply_default_headers(request: "Request") -> None:
    """Inject system-level default headers that the user has not set.

    Iterates ``_SYSTEM_DEFAULTS`` and, for each entry whose key is absent from
    ``request.headers``, sets the header.  Callable values are invoked exactly
    once per call so that dynamic defaults (e.g. UUIDs) are unique per request.

    Args:
        request: The outbound :class:`~equinox.core.request.Request` whose
            ``headers`` dict is mutated in-place.  The caller retains ownership.
    """
    existing = {str(k).lower() for k in request.headers.keys()}
    for name, value_or_factory in _SYSTEM_DEFAULTS.items():
        if name.lower() not in existing:
            request.headers[name] = (
                value_or_factory() if callable(value_or_factory) else value_or_factory
            )

