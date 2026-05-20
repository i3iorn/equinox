"""Save-dialog orchestration mixin for RequestPanel."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtWidgets import QDialog, QMessageBox

from equinox.gui.request_panel.save_dialog import SaveRequestDialog

logger = logging.getLogger(__name__)


class RequestSaveFlowMixin:
    """Encapsulate save-to-collection workflow and side effects."""

    current_request: Any
    _request_persistence: Any

    def _build_request_editor_snapshot(self) -> Any: ...
    def _build_request_from_editor(self, name: str, collection_id: int, folder: str) -> Any: ...
    def _clear_dirty(self) -> None: ...
    def _status_message(self, message: str) -> None: ...

    def _save_request(self) -> bool:
        """Save the current editor state to a collection (prompts for name / folder)."""
        snapshot = self._build_request_editor_snapshot()
        url = snapshot.url
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a URL before saving.")
            return False

        method = snapshot.method
        current_folder = snapshot.folder or ""
        logger.debug(
            "request_panel.save_dialog_open op=save_request method=%s url=%s",
            method,
            url,
        )

        collections = self._request_persistence.list_save_collections()
        dlg = SaveRequestDialog(collections, method, url, current_folder, parent=self)
        logger.debug("request_panel.save_dialog_created op=save_request")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False

        name, col_id, col_name, folder = dlg.result_values()
        folder_value = folder or ""
        logger.debug(
            "request_panel.save_dialog_values op=save_request name=%s collection_id=%s folder=%s",
            name,
            col_id,
            folder_value,
        )

        try:
            request = self._build_request_from_editor(
                name=name,
                collection_id=col_id,
                folder=folder_value,
            )
            save_result = self._request_persistence.save_request_from_dialog(
                request,
                existing_request_id=snapshot.request_id,
                existing_collection_id=snapshot.collection_id,
                target_collection_id=col_id,
                name=name,
            )
            req_id = save_result.request_id
            if save_result.updated_existing:
                logger.info(
                    "request_panel.request_updated op=save_request request_id=%d collection_id=%d method=%s url=%s",
                    req_id,
                    col_id,
                    method,
                    url,
                )
            else:
                logger.info(
                    "request_panel.request_saved op=save_request request_id=%d collection_id=%d method=%s url=%s",
                    req_id,
                    col_id,
                    method,
                    url,
                )

            self.current_request = request
            self._clear_dirty()
            self._status_message(f"Saved '{name}' to '{col_name}'")

            try:
                win = self.window()
                if hasattr(win, "collections_panel"):
                    win.collections_panel.refresh()
            except Exception:
                logger.debug("Failed to refresh collections panel after save", exc_info=True)
            return True
        except Exception as exc:
            logger.error("Failed to save request", exc_info=True)
            QMessageBox.critical(self, "Save Failed", str(exc))
            return False
