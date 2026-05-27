"""User-command and shortcut mixin for RequestPanel."""
# mypy: disable-error-code=arg-type
from __future__ import annotations

import logging
from typing import Any
from typing import TYPE_CHECKING

from equinox.core.request import Request
from equinox.gui.workers import BenchmarkDialog
from PyQt6.QtCore import QObject
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtGui import QShortcut
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QInputDialog
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class RequestCommandsMixin:
    """Keyboard shortcuts and action handlers extracted from RequestPanel."""

    url_input: QLineEdit
    cert_path_input: QLineEdit
    cert_key_input: QLineEdit
    method_combo: QComboBox
    body_type_combo: QComboBox
    verify_ssl_check: QCheckBox
    follow_redirects_check: QCheckBox
    headers_table: Any
    params_table: Any
    body_text: Any
    timeout_spin: Any
    tabs: Any
    _cookie_manager: Any

    if TYPE_CHECKING:

        def _send_request(self) -> None: ...
        def _save_request(self) -> bool: ...
        def _format_json_body(self) -> None: ...
        @staticmethod
        def _detect_body_type(body_text: str, headers: dict[str, str] | None = None) -> str: ...
        def _mark_dirty(self) -> None: ...
        def _status_message(self, message: str, timeout_ms: int = ...) -> None: ...
        def _update_tab_labels(self, *_args: Any) -> None: ...

    def _as_qobject(self) -> QObject:
        return self  # type: ignore[return-value]

    def _as_qwidget(self) -> QWidget:
        return self  # type: ignore[return-value]

    def _setup_shortcuts(self) -> None:
        """Register panel-wide keyboard shortcuts."""
        send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self._as_qobject())
        send_shortcut.activated.connect(self._send_request)

        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self._as_qobject())
        save_shortcut.activated.connect(self._save_request)

        focus_url_shortcut = QShortcut(QKeySequence("Ctrl+L"), self._as_qobject())
        focus_url_shortcut.activated.connect(self._focus_url_input)

        fmt_shortcut = QShortcut(QKeySequence("Ctrl+Shift+F"), self._as_qobject())
        fmt_shortcut.activated.connect(self._format_json_body)

        next_tab_shortcut = QShortcut(QKeySequence("Ctrl+PgDown"), self._as_qobject())
        next_tab_shortcut.activated.connect(lambda: self._cycle_request_tab(1))

        prev_tab_shortcut = QShortcut(QKeySequence("Ctrl+PgUp"), self._as_qobject())
        prev_tab_shortcut.activated.connect(lambda: self._cycle_request_tab(-1))

    def _focus_url_input(self) -> None:
        """Focus URL input and select its full text (browser-like behavior)."""
        self.url_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.url_input.selectAll()

    def _cycle_request_tab(self, step: int) -> None:
        """Move focus to the previous/next request tab."""
        tabs = getattr(self, "tabs", None)
        if tabs is None or tabs.count() <= 1:
            return
        current = tabs.currentIndex()
        tabs.setCurrentIndex((current + step) % tabs.count())

    def _browse_file_to_input(self, title: str, filters: str, target: QLineEdit) -> None:
        """Open a file-picker dialog and write the chosen path into target line edit."""
        from PyQt6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self._as_qwidget(), title, "", filters)
        if path:
            target.setText(path)
            logger.debug("File selected via '%s'", title)

    def _browse_cert(self) -> None:
        self._browse_file_to_input(
            "Select Certificate File",
            "Certificate files (*.pem *.crt *.cer);;All files (*)",
            self.cert_path_input,
        )

    def _browse_cert_key(self) -> None:
        self._browse_file_to_input(
            "Select Private Key File",
            "Key files (*.pem *.key);;All files (*)",
            self.cert_key_input,
        )

    def _import_from_curl(self) -> None:
        """Open a dialog to paste a cURL command and populate the editor."""
        from equinox.core.io.curl_parser import parse_curl

        logger.debug("cURL import dialog opened")
        clipboard = QApplication.clipboard()
        clipboard_text = clipboard.text().strip() if clipboard is not None else ""
        prefill = clipboard_text if clipboard_text.lower().startswith("curl ") else ""

        text, ok = QInputDialog.getMultiLineText(
            self._as_qwidget(),
            "Import from cURL" "Paste a cURL command:",
            prefill,
        )
        if not ok or not text.strip():
            logger.debug("cURL import cancelled by user")
            return

        try:
            parsed = parse_curl(text.strip())
        except Exception as exc:
            logger.warning("Failed to parse cURL command (len=%d): %s", len(text), exc)
            QMessageBox.warning(
                self._as_qwidget(), "Parse Error", f"Could not parse cURL command:\n{exc}",
            )
            return

        method = parsed.get("method", "GET")
        url = parsed.get("url", "")
        headers = parsed.get("headers") or {}
        body = parsed.get("body")

        idx = self.method_combo.findText(method)
        if idx >= 0:
            self.method_combo.setCurrentIndex(idx)
        self.url_input.setText(url)
        self.headers_table.set_data(headers)

        if body is not None:
            body_text = body if isinstance(body, str) else str(body)
            self.body_text.setPlainText(body_text)
            self.body_type_combo.setCurrentText(self._detect_body_type(body_text, headers))
        else:
            self.body_text.clear()
            self.body_type_combo.setCurrentIndex(0)

        if not parsed.get("verify_ssl", True):
            self.verify_ssl_check.setChecked(False)

        self._mark_dirty()
        self._status_message("Request imported from cURL command")
        logger.info("cURL import: %s %s (%d headers)", method, url, len(headers))

    def _open_benchmark(self) -> None:
        """Open the benchmark dialog for the currently configured request."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(
                self._as_qwidget(), "No Request", "Enter a URL before running a benchmark.",
            )
            return

        method = self.method_combo.currentText()
        headers = self.headers_table.get_data()
        body_type = self.body_type_combo.currentText()
        body = (
            self.body_text.toPlainText().strip() or None
            if body_type not in ("none", "multipart/form-data", "GraphQL")
            else None
        )
        req = Request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout=self.timeout_spin.value(),
            verify_ssl=self.verify_ssl_check.isChecked(),
            follow_redirects=self.follow_redirects_check.isChecked(),
        )
        logger.debug("Opening benchmark: %s %s", method, url)
        try:
            BenchmarkDialog(req, self, cookie_manager=self._cookie_manager).exec()
        except Exception:
            logger.error("Failed to open benchmark dialog", exc_info=True)

    @staticmethod
    def _set_all_checkable(table: Any, enabled: bool) -> None:
        """Enable or disable every row in a checkable key-value table."""
        state = Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
        for row in range(table.rowCount()):
            chk_item = table.item(row, 0)
            if chk_item is not None:
                chk_item.setCheckState(state)

    def _insert_header_preset(self, key: str, value: str) -> None:
        """Append a header preset row, or navigate to existing matching key."""
        for row in range(self.headers_table.rowCount()):
            key_item = self.headers_table.item(row, 1)
            if key_item and key_item.text().strip().lower() == key.lower():
                self.headers_table.setCurrentCell(row, 2)
                return
        self.headers_table.add_row(key, value)
        self._mark_dirty()
        self._update_tab_labels()

    def _add_row_and_focus(self, table: Any) -> None:
        """Append an empty row to table, select key cell, and mark dirty."""
        table.add_row("", "", enabled=True)
        last = table.rowCount() - 2
        if last >= 0:
            table.setCurrentCell(last, 1)
            item = table.item(last, 1)
            if item:
                table.editItem(item)
        self._mark_dirty()
        self._update_tab_labels()

    def _remove_table_rows(self, table: Any) -> None:
        rows_to_remove = sorted({idx.row() for idx in table.selectedIndexes()}, reverse=True)
        for row in rows_to_remove:
            table.removeRow(row)
        self._mark_dirty()
        self._update_tab_labels()

    def _headers_add_row(self) -> None:
        self._add_row_and_focus(self.headers_table)

    def _headers_remove_row(self) -> None:
        self._remove_table_rows(self.headers_table)

    def _params_add_row(self) -> None:
        self._add_row_and_focus(self.params_table)

    def _params_remove_row(self) -> None:
        self._remove_table_rows(self.params_table)

    def _params_set_all(self, enabled: bool) -> None:
        self._set_all_checkable(self.params_table, enabled)

    def _headers_set_all(self, enabled: bool) -> None:
        self._set_all_checkable(self.headers_table, enabled)
