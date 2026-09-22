"""Runtime toggle for history body capture.

Allows enabling/disabling storage of request/response bodies in history
for privacy and data-retention purposes.  The module-level functions
retain backward compatibility; new code should inject ``HistoryConfig``.
"""

from __future__ import annotations

import threading

from equinox.core.config import flags

_CAPTURE_BODIES_DEFAULT = True


class HistoryConfig:
    """Injectable history-capture configuration.

    Replaces the process-global ``_HistoryCaptureState`` with an explicit
    instance that can be shared or isolated per test / service.
    """

    def __init__(self, capture_bodies: bool | None = None) -> None:
        self._lock = threading.Lock()
        self._capture_bodies: bool | None = capture_bodies

    def get(self) -> bool:
        with self._lock:
            if self._capture_bodies is not None:
                return bool(self._capture_bodies)
            env_value = flags.is_history_capture_enabled()
            self._capture_bodies = _CAPTURE_BODIES_DEFAULT if env_value is None else bool(env_value)
            return bool(self._capture_bodies)

    def set(self, value: bool) -> None:
        with self._lock:
            self._capture_bodies = bool(value)

    def reset(self) -> None:
        with self._lock:
            self._capture_bodies = None


# Default instance for backward-compatible module-level API.
_DEFAULT_CONFIG = HistoryConfig()


def should_capture_bodies() -> bool:
    return _DEFAULT_CONFIG.get()


def set_capture_bodies(value: bool) -> None:
    _DEFAULT_CONFIG.set(value)


def reset_capture_bodies() -> None:
    """Reset runtime override so next read uses environment/default value."""
    _DEFAULT_CONFIG.reset()
