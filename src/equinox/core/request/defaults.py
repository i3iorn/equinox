"""Default system-level headers applied to every outbound request.

This module is part of ``core/`` so it can be imported by the application
service layer without triggering the GUI package chain.

``gui/request_panel/mixins/_default_headers.py`` re-exports ``apply_default_headers``
for backward compatibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Union
from uuid import uuid4

from equinox.versioning import get_app_version

if TYPE_CHECKING:
    from equinox.core.request import Request

# ── Registry of system defaults ───────────────────────────────────────────────

_SYSTEM_DEFAULTS: Dict[str, Union[str, Callable[[], str]]] = {
    # Unique identifier for each outbound request — useful for distributed
    # tracing and server-side correlation logs.
    "X-Request-ID": lambda: str(uuid4()),
    "User-Agent": "Equinox API testing v" + get_app_version(),
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
}


def apply_default_headers(request: "Request") -> None:
    """Inject system-level default headers that the user has not set.

    For each entry in ``_SYSTEM_DEFAULTS`` whose key is absent from
    ``request.headers`` (case-insensitive), the header is added.
    Callable values are invoked once per call so dynamic defaults
    (e.g., UUID correlation IDs) are unique per request.

    Args:
        request: Outbound ``Request`` whose headers dict is mutated in-place.
    """
    existing = {str(k).lower() for k in request.headers.keys()}
    for name, value_or_factory in _SYSTEM_DEFAULTS.items():
        if name.lower() not in existing:
            request.headers[name] = (
                value_or_factory() if callable(value_or_factory) else value_or_factory
            )

