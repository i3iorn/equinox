"""User-action mixin for ResponsePanel.

Codegen, copy, download, diff-with-history, search, word-wrap, and
large-body loading.  Has no ``__init__`` — relies on ``self.*`` attributes
set by ``ResponsePanel.__init__``.
"""

from __future__ import annotations

import difflib
import logging
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QApplication, QDialog, QComboBox,
    QPlainTextEdit, QListWidget, QListWidgetItem, QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt

from equinox.core.codegen import GENERATORS, generate_code
from equinox.gui.response_panel.pretty_print import PrettyPrintRunnable
from equinox.gui.response_panel._formatting import pretty_print_body
from equinox.gui.theme import get_mono_font

if TYPE_CHECKING:
    from equinox.storage import Database

logger = logging.getLogger(__name__)

# Dialog geometry constants
_HISTORY_PICKER_WIDTH = 480
_HISTORY_PICKER_HEIGHT = 280
_DIFF_DIALOG_WIDTH = 700
_DIFF_DIALOG_HEIGHT = 500
_CODEGEN_DIALOG_WIDTH = 640
_CODEGEN_DIALOG_HEIGHT = 480

# File extension mapping by content-type
_CONTENT_TYPE_EXTENSIONS = {
    "json": "response.json",
    "xml": "response.xml",
    "html": "response.html",
}
_DEFAULT_FILENAME = "response.txt"


class ResponseActionsMixin:
    """Mixin providing all user-action methods for ResponsePanel."""

    # ------------------------------------------------------------------
    # Private helpers — encapsulation
    # ------------------------------------------------------------------

    def _get_body_text(self) -> str:
        """Return the current body text, using _pretty_body as fallback.

        Handles None checks and exceptions consistently across all actions.
        """
        displayed = self.body_text.toPlainText()
        if displayed:
            return displayed
        if self.current_response is not None:
            try:
                return pretty_print_body(self.current_response)
            except Exception:
                logger.exception("Failed to pretty-print body")
        return ""

    def _get_database(self) -> "Optional[Database]":
        """Extract database from the main window, or None if unavailable.

        Centralizes the risky window traversal so it can be updated in one place.
        """
        try:
            return self.window().db
        except Exception:
            return None

    def _get_current_request_marker(self) -> object:
        """Return a unique identifier for the current response's request URL.

        Used to detect stale responses in async operations (e.g., large body
        loading). Returns (sent_url or request.url) to match _load_large_body
        and _on_pretty_result.
        """
        if self.current_response is None:
            return None
        return getattr(self.current_response, "sent_url", None) or getattr(
            self.current_response.request, "url", None
        )

    def _suggest_filename(self) -> str:
        """Suggest a filename for download based on content-type.

        Returns a filename with appropriate extension for JSON/XML/HTML,
        or a default .txt filename.
        """
        if self.current_response is None:
            return _DEFAULT_FILENAME

        ct = self.current_response.headers.get("content-type", "").lower()
        for key, filename in _CONTENT_TYPE_EXTENSIONS.items():
            if key in ct:
                return filename
        return _DEFAULT_FILENAME

    # ------------------------------------------------------------------
    # Large body loading
    # ------------------------------------------------------------------

    def _load_large_body(self) -> None:
        """User confirmed loading a large body."""
        if self.current_response is None:
            return

        self._body_warning.setVisible(False)
        self._loading_label.setVisible(True)

        marker = self._get_current_request_marker()
        runnable = PrettyPrintRunnable(self.current_response, marker)
        runnable.signals.result.connect(self._on_pretty_result)
        self._thread_pool.start(runnable)

    def _on_pretty_result(self, marker: object, formatted_text: str) -> None:
        """Handle formatted body results from background worker."""
        self._loading_label.setVisible(False)

        # Stale response — user switched requests while formatting was in progress.
        if marker != self._get_current_request_marker():
            return

        try:
            self._pretty_body_text = formatted_text
            if self.current_response is not None:
                self._raw_body_text = self._decode_response_body(self.current_response)
            self._render_body_by_mode(getattr(self, "_readability_mode", "pretty"))
        except Exception:
            logger.exception("Failed to display formatted body; falling back to raw")
            try:
                fallback = self._get_body_text()
                self.body_text.set_code(fallback)
            except Exception:
                logger.exception("Fallback body display also failed")
                self.body_text.clear()

    # ------------------------------------------------------------------
    # JSONPath filter callback
    # ------------------------------------------------------------------

    def _on_jsonpath_filter(self, filtered_text: Optional[str]) -> None:
        """Receive filtered JSON text from the SearchBar and update the body view."""
        try:
            if filtered_text is None:
                self.body_text.set_code(self._get_body_text())
            else:
                self.body_text.set_code(filtered_text)
        except Exception:
            logger.exception("Failed to update body with JSONPath filter")
            self.body_text.set_code(self._get_body_text())

    # ------------------------------------------------------------------
    # Diff with history
    # ------------------------------------------------------------------

    def _diff_with_history(self) -> None:
        """Open a dialog to compare the current response body with a history entry."""
        if self.current_response is None:
            return

        history_entries = self._fetch_history_entries()
        if not history_entries:
            QMessageBox.information(
                self,
                "Diff vs. History",
                "No matching history entries found for this request.",
            )
            return

        entry = self._pick_history_entry(history_entries)
        if entry is None:
            return

        old_body = entry.get("response_body") or ""
        new_body = self._get_body_text()
        self._show_diff_dialog(old_body, new_body)

    def _fetch_history_entries(self) -> list:
        """Return recent history entries matching the current request.

        Returns an empty list if the database is unavailable or the query fails.
        """
        db = self._get_database()
        if db is None or self.current_response is None:
            return []

        try:
            from equinox.storage import HistoryManager
            req = self.current_response.request
            return HistoryManager(db).search_history(
                query=req.url, method=req.method, limit=30
            )
        except Exception:
            logger.exception("Failed to fetch history entries for diff")
            return []

    def _format_history_entry(self, entry: dict) -> str:
        """Format a history entry for display in the picker list."""
        ts = entry.get("executed_at", "")[:19]
        method = entry.get("method", "")
        url = entry.get("url", "")
        status = entry.get("status_code", "?")
        return f"{ts}  {method}  {url}  [{status}]"

    def _pick_history_entry(self, history_entries: list) -> "Optional[dict]":
        """Show a picker dialog and return the selected history entry, or None."""
        picker = QDialog(self)
        picker.setWindowTitle("Choose History Entry")
        picker.setMinimumSize(_HISTORY_PICKER_WIDTH, _HISTORY_PICKER_HEIGHT)
        pk_layout = QVBoxLayout(picker)
        pk_layout.addWidget(QLabel("Select a history entry to compare against:"))

        list_widget = QListWidget()
        for entry in history_entries:
            label = self._format_history_entry(entry)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            list_widget.addItem(item)
        pk_layout.addWidget(list_widget, 1)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        compare_btn = QPushButton("Compare")
        compare_btn.setEnabled(False)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(compare_btn)
        pk_layout.addLayout(btn_row)

        cancel_btn.clicked.connect(picker.reject)
        compare_btn.clicked.connect(picker.accept)
        list_widget.currentItemChanged.connect(
            lambda cur, _: compare_btn.setEnabled(cur is not None)
        )

        if picker.exec() != QDialog.DialogCode.Accepted:
            return None
        selected = list_widget.currentItem()
        return selected.data(Qt.ItemDataRole.UserRole) if selected else None

    def _show_diff_dialog(self, old_body: str, new_body: str) -> None:
        """Display a unified diff between *old_body* and *new_body*."""
        old_lines = old_body.splitlines(keepends=True)
        new_lines = new_body.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                old_lines, new_lines, fromfile="History", tofile="Current", lineterm=""
            )
        )
        diff_text = "".join(diff_lines) if diff_lines else "(No differences)"

        dlg = QDialog(self)
        dlg.setWindowTitle("Response Body Diff")
        dlg.setMinimumSize(_DIFF_DIALOG_WIDTH, _DIFF_DIALOG_HEIGHT)
        dv_layout = QVBoxLayout(dlg)
        diff_editor = QPlainTextEdit()
        diff_editor.setReadOnly(True)
        diff_editor.setFont(get_mono_font())
        diff_editor.setPlainText(diff_text)
        dv_layout.addWidget(diff_editor, 1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        dv_layout.addWidget(close_btn)
        dlg.exec()

    # ------------------------------------------------------------------
    # Copy / Download / Codegen
    # ------------------------------------------------------------------

    def _copy_body(self) -> None:
        """Copy the current body text to the clipboard."""
        text = self._get_body_text()
        if not text:
            return
        QApplication.clipboard().setText(text)

    def _download_body(self) -> None:
        """Save the current body text to a file."""
        if self.current_response is None:
            return

        suggested_filename = self._suggest_filename()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Response Body",
            suggested_filename,
            "All Files (*.*)",
        )
        if not path:
            return

        payload = self._response_bytes_for_download()
        try:
            with open(path, "wb") as f:
                f.write(payload)
            logger.info(
                "response_panel.download_body_saved path=%s size_bytes=%d",
                path,
                len(payload),
            )
        except Exception as exc:
            logger.warning("response_panel.download_body_failed path=%s error=%s", path, exc)
            QMessageBox.warning(self, "Save Failed", f"Could not save file:\n{exc}")

    def _response_bytes_for_download(self) -> bytes:
        """Return exact response bytes for file download, with safe text fallback."""
        if self.current_response is None:
            return b""
        body = getattr(self.current_response, "body", b"")
        if isinstance(body, (bytes, bytearray)):
            return bytes(body)
        if isinstance(body, str):
            return body.encode("utf-8", errors="replace")
        text = self._get_body_text()
        return text.encode("utf-8", errors="replace")

    def _generate_code_snippet(self, fmt: str) -> str:
        """Generate client code for the request in the given *fmt*.

        Returns the generated code, or an error message on failure.
        """
        if self.current_response is None:
            return "# No response available"
        try:
            return generate_code(fmt, self.current_response)
        except Exception as exc:
            return f"# Error generating code: {exc}"

    def _copy_as_code(self, fmt: str) -> None:
        """Generate client code for this request and copy to clipboard."""
        code = self._generate_code_snippet(fmt)
        if code.startswith("# Error"):
            QMessageBox.warning(self, "Code Generation Failed", code)
        else:
            QApplication.clipboard().setText(code)

    def _view_code_dialog(self) -> None:
        """Show a dialog with generated client code in multiple languages."""
        if self.current_response is None:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Generate Client Code")
        dlg.setMinimumSize(_CODEGEN_DIALOG_WIDTH, _CODEGEN_DIALOG_HEIGHT)
        layout = QVBoxLayout(dlg)

        row = QHBoxLayout()
        row.addWidget(QLabel("Language / Format:"))
        combo = QComboBox()
        combo.addItems(list(GENERATORS.keys()))
        row.addWidget(combo, 1)
        layout.addLayout(row)

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setFont(get_mono_font())
        layout.addWidget(editor, 1)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy")
        close_btn = QPushButton("Close")
        btn_row.addStretch()
        btn_row.addWidget(copy_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        def update_code() -> None:
            fmt = combo.currentText()
            code = self._generate_code_snippet(fmt)
            editor.setPlainText(code)

        combo.currentIndexChanged.connect(update_code)
        update_code()

        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(editor.toPlainText()))
        close_btn.clicked.connect(dlg.accept)

        dlg.exec()

    def _copy_as_curl(self) -> None:
        """Copy the request as a cURL command."""
        self._copy_as_code("cURL")

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _toggle_word_wrap(self, checked: bool) -> None:
        """Toggle line wrapping in the response body text view."""
        mode = QTextEdit.LineWrapMode.WidgetWidth if checked else QTextEdit.LineWrapMode.NoWrap
        self.body_text.setLineWrapMode(mode)

    def _open_search(self) -> None:
        """Open the inline search bar when Ctrl+F is pressed."""
        if self.tabs.currentIndex() != self._body_tab_idx:
            self.tabs.setCurrentIndex(self._body_tab_idx)
        self._search_bar.show_and_focus()

