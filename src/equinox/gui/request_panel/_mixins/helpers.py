"""Module-level helper functions shared by the request-panel mixins."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def notify_log_panel(log_panel: Any, method: str, *args: Any) -> None:
    """Call a logging-panel method safely.

    Args:
        log_panel: LoggingPanel instance or ``None``
        method: Method name to call (e.g. ``"log_request"``)
        *args: Arguments forwarded to the method
    """
    if log_panel is None:
        return
    try:
        getattr(log_panel, method)(*args)
    except Exception:
        logger.debug("Failed to call log_panel.%s", method, exc_info=True)
