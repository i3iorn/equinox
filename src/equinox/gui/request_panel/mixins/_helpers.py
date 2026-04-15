"""Module-level helper functions shared by the request-panel mixins.

These are intentionally free functions — they have no ``self`` and can
be called from any context (including deferred ``QTimer`` callbacks).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from equinox.core.request import Request, Response
from equinox.storage import Database, HistoryManager

from equinox.gui.request_panel.mixins._constants import FOLDER_AUTH_PREFIX

logger = logging.getLogger(__name__)


def save_history_safe(
    db: Database,
    request: Request,
    response: Optional[Response] = None,
    error: Optional[str] = None,
) -> None:
    """Persist request/response to history without letting exceptions bubble to UI.

    Args:
        db: Database instance
        request: Request to save (skipped if None)
        response: Successful response (optional)
        error: Error message (optional, used when response is None)
    """
    if request is None:
        return
    try:
        mgr = HistoryManager(db)
        if response is not None:
            mgr.save_history(request, response)
        elif error is not None:
            mgr.save_history(request, error=error)
    except Exception:
        logger.debug("Failed to save history", exc_info=True)


def write_auth_to_source(mgr: Any, collection_id: int, source: str, auth: Any) -> None:
    """Persist auth to the collection or folder identified by *source*.

    Shared by ``_persist_inherited_auth_tokens`` and
    ``_save_inherited_token_to_source`` so the source→manager dispatch
    is defined in exactly one place.

    Args:
        mgr: CollectionManager instance
        collection_id: Collection ID
        source: Source identifier (``"collection"`` or ``"folder:<name>"``)
        auth: Auth strategy to persist
    """
    if source == "collection":
        mgr.set_collection_auth(collection_id, auth)
    elif source.startswith(FOLDER_AUTH_PREFIX):
        folder_name = source[len(FOLDER_AUTH_PREFIX):]
        mgr.set_folder_auth(collection_id, folder_name, auth)
    else:
        logger.warning("Unknown auth source: %s — cannot persist", source)


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

