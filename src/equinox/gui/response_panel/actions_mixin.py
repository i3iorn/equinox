"""User-action mixin for ResponsePanel.

Codegen, copy, download, diff-with-history, search, word-wrap, and
large-body loading.  Has no ``__init__`` — relies on ``self.*`` attributes
set by ``ResponsePanel.__init__``.
"""

from __future__ import annotations

import difflib
import logging
from typing import Optional, Dict, Any

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QApplication, QDialog, QComboBox,
    QPlainTextEdit, QListWidget, QListWidgetItem, QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt

from equinox.core.codegen import GENERATORS, generate_code
from equinox.gui.response_panel.pretty_print import PrettyPrintRunnable
from equinox.gui.theme import get_mono_font

logger = logging.getLogger(__name__)


class ResponseActionsMixin:
    """Mixin providing all user-action methods for ResponsePanel."""

    # ------------------------------------------------------------------
    # Large body loading
    # ------------------------------------------------------------------

    def _load_large_body(self) -> None:
        """User confirmed loading a large body."""
        if self.current_response is None:
            return

        self._body_warning.setVisible(False)
        self._loading_label.setVisible(True)

        marker = getattr(self.current_response, "sent_url", None) or getattr(
            self.current_response.request, "url", None
        )
        runnable = PrettyPrintRunnable(self.current_response, marker)
        runnable.signals.result.connect(self._on_pretty_result)
        self._thread_pool.start(runnable)

    def _on_pretty_result(self, marker: object, formatted_text: str) -> None:
        """Handle formatted body results from background worker."""
        self._loading_label.setVisible(False)
        try:
            cur_marker = getattr(self.current_response, "sent_url", None) or getattr(
                self.current_response.request, "url", None
            )
            if cur_marker != marker:
                return
            self.body_text.set_code(formatted_text)
        except Exception:
            logger.exception("Unexpected error in _on_pretty_result; falling back to raw body")
            if self.current_response is not None:
                try:
                    self.body_text.set_code(self._pretty_body(self.current_response))
                except Exception:
                    logger.exception("Fallback body display also failed")
            else:
                self.body_text.clear()

    # ------------------------------------------------------------------
    # JSONPath filter callback
    # ------------------------------------------------------------------

    def _on_jsonpath_filter(self, filtered_text: Optional[str]) -> None:
        """Receive filtered JSON text from the SearchBar and update the body view."""
        try:
            if filtered_text is None:
                if self.current_response is None:
                    self.body_text.clear()
                else:
                    self.body_text.set_code(self._pretty_body(self.current_response))
            else:
                self.body_text.set_code(filtered_text)
        except Exception:
            # If anything goes wrong, fall back to original body
            if self.current_response is not None:
                self.body_text.set_code(self._pretty_body(self.current_response))
            else:
                self.body_text.clear()

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
        displayed = self.body_text.toPlainText()
        new_body = displayed if displayed else self._pretty_body(self.current_response)
        self._show_diff_dialog(old_body, new_body)

    def _fetch_history_entries(self) -> list:
        """Return recent history entries matching the current request, or []."""
        db = None
        try:
            db = self.window().db
        except Exception:
            pass
        if db is None:
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

    def _pick_history_entry(self, history_entries: list) -> "Optional[Dict[str, Any]]":
        """Show a picker dialog and return the selected history entry, or None."""
        picker = QDialog(self)
        picker.setWindowTitle("Choose History Entry")
        picker.setMinimumSize(480, 280)
        pk_layout = QVBoxLayout(picker)
        pk_layout.addWidget(QLabel("Select a history entry to compare against:"))

        list_widget = QListWidget()
        for entry in history_entries:
            ts = entry.get("executed_at", "")[:19]
            sc = entry.get("status_code", "?")
            label = f"{ts}  {entry.get('method', '')}  {entry.get('url', '')}  [{sc}]"
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
        dlg.setMinimumSize(700, 500)
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
        text = self.body_text.toPlainText()
        if not text:
            return
        QApplication.clipboard().setText(text)

    def _download_body(self) -> None:
        """Save the current body text to a file."""
        if self.current_response is None:
            return

        suggested = "response.txt"
        ct = self.current_response.headers.get("content-type", "").lower()
        if "json" in ct:
            suggested = "response.json"
        elif "xml" in ct:
            suggested = "response.xml"
        elif "html" in ct:
            suggested = "response.html"

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Response Body",
            suggested,
            "All Files (*.*)",
        )
        if not path:
            return

        text = self.body_text.toPlainText() or self._pretty_body(self.current_response)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as exc:
            QMessageBox.warning(self, "Save Failed", f"Could not save file:\n{exc}")

    def _copy_as_code(self, fmt: str) -> None:
        """Generate client code for this request and copy to clipboard."""
        if self.current_response is None:
            return
        try:
            code = generate_code(fmt, self.current_response.request)
        except Exception as exc:
            QMessageBox.warning(self, "Code Generation Failed", str(exc))
            return
        QApplication.clipboard().setText(code)

    def _view_code_dialog(self) -> None:
        """Show a dialog with generated client code."""
        if self.current_response is None:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Generate Client Code")
        dlg.setMinimumSize(640, 480)
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
            try:
                code = generate_code(fmt, self.current_response.request)
            except Exception as exc:
                code = f"# Error generating code: {exc}"
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


