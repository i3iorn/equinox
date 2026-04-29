"""Runtime toggle for history body capture.

Allows enabling/disabling storage of request/response bodies in history
for privacy and data-retention purposes.
"""

from __future__ import annotations

# Centralized flag access - keep simple while enabling future extension
from equinox.core.config import flags
from typing import Optional

_CAPTURE_BODIES_DEFAULT = True

_capture_bodies: Optional[bool] = None


def should_capture_bodies() -> bool:
    """Return whether history should store request/response bodies.

    The default is to capture bodies. If EQUINOX_HISTORY_CAPTURE_BODIES is
    set to a truthy value (1/true/yes), bodies are captured; if set to a
    falsey value (0/no), bodies are omitted from history.
    """
    global _capture_bodies
    if _capture_bodies is not None:
        return bool(_capture_bodies)
    # Use centralized flag reader for consistency
    _capture_bodies = flags.is_history_capture_enabled()  # type: ignore
    if _capture_bodies is None:
        _capture_bodies = _CAPTURE_BODIES_DEFAULT
    return bool(_capture_bodies)


def set_capture_bodies(value: bool) -> None:
    global _capture_bodies
    _capture_bodies = bool(value)
