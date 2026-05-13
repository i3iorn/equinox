"""Save-dialog orchestration mixin for RequestPanel."""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import QDialog, QMessageBox

from equinox.gui.request_panel.save_dialog import SaveRequestDialog

logger = logging.getLogger(__name__)


class RequestSaveFlowMixin:
    """Encapsulate save-to-collection workflow and side effects."""

    def _save_request(self) -> bool:
        """Save the current editor state to a collection (prompts for name / folder)."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a URL before saving.")
            return False

        method = self.method_combo.currentText()
        current_folder = getattr(self.current_request, "folder", None) or ""
        logger.debug(
            "request_panel.save_dialog_open op=save_request method=%s url=%s",
            method,
            url,
        )

        dlg = SaveRequestDialog(self.db, method, url, current_folder, parent=self)
        logger.debug("request_panel.save_dialog_created op=save_request")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False

        name, col_id, col_name, folder = dlg.result_values()
        logger.debug(
            "request_panel.save_dialog_values op=save_request name=%s collection_id=%s folder=%s",
            name,
            col_id,
            folder,
        )

        try:
            existing_req = self.current_request
            existing_id = getattr(existing_req, "id", None)
            existing_collection_id = getattr(existing_req, "collection_id", None)
            request = self._build_request_from_editor(
                name=name,
                collection_id=col_id,
                folder=folder,
            )
            if existing_id and existing_collection_id == col_id:
                request.id = existing_id
                self._collection_mgr.update_request(request)
                req_id = existing_id
                logger.info(
                    "request_panel.request_updated op=save_request request_id=%d collection_id=%d method=%s url=%s",
                    req_id,
                    col_id,
                    method,
                    url,
                )
            else:
                req_id = self._collection_mgr.save_request(request, collection_id=col_id, name=name)
                request.id = req_id
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

