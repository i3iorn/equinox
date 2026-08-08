"""GUI logging helpers for consistent event tracing."""

from __future__ import annotations

import json
import logging
from typing import Any

from equinox.core.util.time import utc_now

logger = logging.getLogger("equinox.gui")


def log_gui_event(
    event: str,
    payload: dict[str, Any] | None = None,
    level: int = logging.INFO,
) -> None:
    """Log a GUI event as a structured JSON payload.

    - event: short event name (e.g. 'window_initialized')
    - payload: optional dict of contextual data
    - level: logging level
    """
    data: dict[str, Any] = {
        "event": event,
        "timestamp": utc_now().isoformat(),
        "payload": payload or {},
    }
    logger.log(level, json.dumps(data, ensure_ascii=False), extra=data)


def notify_log_panel(log_panel: Any, method: str, *args: Any) -> None:
    """Invoke a logging-panel method safely when the panel is available."""
    if log_panel is None:
        return
    try:
        getattr(log_panel, method)(*args)
    except Exception:
        logger.exception("Failed to call log_panel.%s", method, exc_info=True)
