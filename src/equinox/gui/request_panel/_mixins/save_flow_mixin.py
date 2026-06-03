from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from typing import cast
from typing import TYPE_CHECKING

from equinox.gui.dialogs.save_dialog import SaveRequestDialog
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


# -----------------------------
# Domain Models
# -----------------------------


@dataclass(frozen=True)
class SaveDialogResult:
    """Validated result from the save dialog."""

    name: str
    collection_id: int
    collection_name: str
    folder: str


# -----------------------------
# Custom Exceptions
# -----------------------------


class SaveRequestError(Exception):
    """Raised when saving a request fails safely."""


# -----------------------------
# Mixin
# -----------------------------


class RequestSaveFlowMixin:
    """Encapsulate save-to-collection workflow and side effects."""

    current_request: Any
    _request_persistence: Any

    if TYPE_CHECKING:

        def _build_request_editor_snapshot(self) -> Any: ...
        def _build_request_from_editor(self, **overrides: Any) -> Any: ...
        def _clear_dirty(self) -> None: ...
        def _status_message(self, message: str, timeout_ms: int = ...) -> None: ...

    def _as_qwidget(self) -> QWidget:
        return cast(QWidget, cast(object, self))

    # ============================================================
    # Public Orchestration Method
    # ============================================================

    def _save_request(self) -> bool:
        """Save the current editor state to a collection."""
        snapshot = self._build_request_editor_snapshot()

        if not _is_valid_url(snapshot.url):
            QMessageBox.warning(self._as_qwidget(), "Missing URL", "Please enter a URL before saving.")
            return False

        logger.debug(
            "request_panel.save_dialog_open op=save_request method=%s url=%s",
            snapshot.method,
            snapshot.url,
        )

        try:
            dialog_result = _open_save_dialog(self, snapshot)
            request = _build_request_object(self, dialog_result)
            save_result = _persist_request(self, snapshot, dialog_result, request)
            _finalize_save(self, snapshot, dialog_result, save_result, request)
            return True

        except SaveRequestError as exc:
            logger.error("Failed to save request", exc_info=True)
            QMessageBox.critical(self._as_qwidget(), "Save Failed", str(exc))
            return False


# ============================================================
# Validation Helpers
# ============================================================


def _is_valid_url(url: str | None) -> bool:
    """Return True only if URL is a non-empty string."""
    return isinstance(url, str) and url.strip() != ""


# ============================================================
# Dialog Handling
# ============================================================


def _open_save_dialog(self: RequestSaveFlowMixin, snapshot: Any) -> SaveDialogResult:
    """Open the save dialog and return validated user input."""
    collections = self._request_persistence.list_save_collections()

    dialog = SaveRequestDialog(
        collections,
        snapshot.method,
        snapshot.url,
        snapshot.folder or "",
        parent=self._as_qwidget(),
    )

    logger.debug("request_panel.save_dialog_created op=save_request")

    if dialog.exec() != QDialog.DialogCode.Accepted:
        raise SaveRequestError("User cancelled save dialog.")

    name, col_id, col_name, folder = dialog.result_values()
    folder_value = folder or ""

    logger.debug(
        "request_panel.save_dialog_values op=save_request name=%s collection_id=%s folder=%s",
        name,
        col_id,
        folder_value,
    )

    return SaveDialogResult(
        name=name,
        collection_id=col_id,
        collection_name=col_name,
        folder=folder_value,
    )


# ============================================================
# Request Construction
# ============================================================


def _build_request_object(self: RequestSaveFlowMixin, dialog_result: SaveDialogResult) -> Any:
    """Build a request object from the editor state and dialog values."""
    try:
        return self._build_request_from_editor(
            name=dialog_result.name,
            collection_id=dialog_result.collection_id,
            folder=dialog_result.folder,
        )
    except Exception as exc:
        raise SaveRequestError("Unable to build request object.") from exc


# ============================================================
# Persistence Layer
# ============================================================


def _persist_request(
    self: RequestSaveFlowMixin,
    snapshot: Any,
    dialog_result: SaveDialogResult,
    request: Any,
) -> Any:
    """Persist the request using the persistence layer."""
    try:
        return self._request_persistence.save_request_from_dialog(
            request,
            existing_request_id=snapshot.request_id,
            existing_collection_id=snapshot.collection_id,
            target_collection_id=dialog_result.collection_id,
            name=dialog_result.name,
        )
    except Exception as exc:
        raise SaveRequestError("Persistence layer failed to save request.") from exc


# ============================================================
# Finalization & UI Updates
# ============================================================


def _finalize_save(
    self: RequestSaveFlowMixin,
    snapshot: Any,
    dialog_result: SaveDialogResult,
    save_result: Any,
    request: Any,
) -> None:
    """Update UI, logs, and internal state after a successful save."""
    req_id = save_result.request_id
    method = snapshot.method
    url = snapshot.url

    log_event = (
        "request_panel.request_updated"
        if save_result.updated_existing
        else "request_panel.request_saved"
    )

    logger.info(
        "%s op=save_request request_id=%d collection_id=%d method=%s url=%s",
        log_event,
        req_id,
        dialog_result.collection_id,
        method,
        url,
    )

    self.current_request = request
    self._clear_dirty()
    self._status_message(f"Saved '{dialog_result.name}' to '{dialog_result.collection_name}'")

    _refresh_collections_panel(self)


def _refresh_collections_panel(self: RequestSaveFlowMixin) -> None:
    """Refresh the collections panel safely."""
    try:
        win = self._as_qwidget().window()
        if win is not None and hasattr(win, "collections_panel"):
            win.collections_panel.refresh()
    except Exception:
        logger.exception("Failed to refresh collections panel after save", exc_info=True)
