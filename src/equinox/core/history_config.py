"""Runtime toggle for history body capture.

Allows enabling/disabling storage of request/response bodies in history
for privacy and data-retention purposes.
"""

from __future__ import annotations

from equinox.core.config import flags
import threading
from typing import Optional

_CAPTURE_BODIES_DEFAULT = True

class _HistoryCaptureState:
    """Thread-safe holder for runtime history-capture overrides."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._capture_bodies: Optional[bool] = None

    def get(self) -> bool:
        with self._lock:
            if self._capture_bodies is not None:
                return bool(self._capture_bodies)
            env_value = flags.is_history_capture_enabled()
            self._capture_bodies = (
                _CAPTURE_BODIES_DEFAULT if env_value is None else bool(env_value)
            )
            return bool(self._capture_bodies)

    def set(self, value: bool) -> None:
        with self._lock:
            self._capture_bodies = bool(value)

    def reset(self) -> None:
        with self._lock:
            self._capture_bodies = None


_STATE = _HistoryCaptureState()


def should_capture_bodies() -> bool:
    """Return whether history should store request/response bodies.

    The default is to capture bodies. If EQUINOX_HISTORY_CAPTURE_BODIES is
    set to a truthy value (1/true/yes), bodies are captured; if set to a
    falsey value (0/no), bodies are omitted from history.
    """
    return _STATE.get()


def set_capture_bodies(value: bool) -> None:
    _STATE.set(value)


def reset_capture_bodies() -> None:
    """Reset runtime override so next read uses environment/default value."""
    _STATE.reset()

