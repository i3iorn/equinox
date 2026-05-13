"""Autosave and dirty-state helpers for RequestPanel."""
from __future__ import annotations

import logging
from typing import Dict, Optional

from equinox.core.request import Request
from equinox.gui.request_panel._constants import STATUS_DURATION_LONG

logger = logging.getLogger(__name__)


class RequestAutosaveMixin:
    """Encapsulates request editor serialization and autosave behavior."""

    def is_dirty(self) -> bool:
        return self._dirty

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _status_message(self, text: str, timeout_ms: int = 5000) -> None:
        """Show a message in the main window status bar (best-effort)."""
        try:
            self.window().statusBar().showMessage(text, timeout_ms)
        except Exception:
            logger.debug("Could not show status message: %s", text)

    def _build_request_from_editor(self, **overrides) -> Request:
        """Construct a Request from the current editor widget state."""
        fields = dict(
            method=self.method_combo.currentText(),
            url=self.url_input.text().strip(),
            headers=self.headers_table.get_data(),
            params=self.params_table.get_enabled_data(),
            params_list=self.params_table.get_all_rows(),
            body=self.body_text.toPlainText().strip() or None,
            auth=self._auth,
            timeout=self.timeout_spin.value(),
            verify_ssl=self.verify_ssl_check.isChecked(),
            follow_redirects=self.follow_redirects_check.isChecked(),
            captures=self._get_captures(),
            assertions=self._get_assertions(),
            pre_script=self.pre_script_editor.toPlainText(),
            post_script=self.post_script_editor.toPlainText(),
            cert_path=self.cert_path_input.text().strip() or None,
            cert_key_path=self.cert_key_input.text().strip() or None,
            description=self.notes_editor.toPlainText().strip() or None,
            path_params=self.path_params_table.get_all_data(),
        )
        fields.update(overrides)
        return Request(**fields)

    def autosave_current(self) -> None:
        """Persist the current editor state back to the DB if dirty."""
        if not self._dirty:
            return
        req = self.current_request
        if not req or not getattr(req, "id", None):
            return
        try:
            updated = self._build_request_from_editor(
                name=req.name,
                collection_id=req.collection_id,
                folder=req.folder,
                id=req.id,
            )
            self._collection_mgr.update_request(updated)
            self._clear_dirty()
            logger.debug("Autosaved request id=%s %s %s", req.id, updated.method, updated.url)
        except Exception:
            logger.error("Autosave failed for request id=%s", getattr(req, "id", None), exc_info=True)
            self._status_message("Autosave failed - click Save to preserve changes", STATUS_DURATION_LONG)

    def save_current_request(self) -> bool:
        """Public wrapper for the save dialog flow."""
        return self._save_request()

