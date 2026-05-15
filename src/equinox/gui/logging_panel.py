"""Request/Response logging panel."""
from __future__ import annotations

import json
import logging
from typing import Any

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout,
    QTextEdit, QPushButton, QComboBox, QSplitter,
    QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from equinox.security import redact_body, redact_headers, redact_url
from equinox.core.util.time import utc_now
from equinox.gui.theme import Colors, get_mono_font
from equinox.gui.log_file_actions import show_log_file_open_result, try_open_current_log_file
from equinox.gui.ui_common import (
    configure_splitter_persistence,
    create_muted_label,
    create_panel_layout,
    get_gui_settings,
)

__all__ = ["LoggingPanel"]

# Routes GUI traffic to the structured log file as well as the panel.
_py_logger = logging.getLogger("equinox.gui.traffic")

MAX_LOG_ENTRIES: int = 500

# ── Private constants ─────────────────────────────────────────────────────────

_BODY_REDACT_LEN: int = 2_000
_ERROR_REDACT_LEN: int = 500
_ERROR_PREVIEW_LEN: int = 60
_TS_SLICE = slice(11, 23)          # "HH:MM:SS.mmm" from ISO-8601 timestamp
_SETTINGS_KEY_SPLITTER: str = "splitter/logging"
_SPLITTER_DEFAULT_SIZES: list[int] = [200, 200]

# Maps combo-box display text → entry["type"] value.
_FILTER_MAP: dict[str, str] = {
    "Requests":  "request",
    "Responses": "response",
    "Errors":    "error",
}


class LoggingPanel(QWidget):
    """Sidebar panel showing a live log of all HTTP transactions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[dict[str, Any]] = []
        self._settings = get_gui_settings()
        self._init_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = create_panel_layout(self)

        toolbar = QHBoxLayout()
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", *_FILTER_MAP.keys()])
        # Lambda drops the int argument emitted by currentIndexChanged.
        self.filter_combo.currentIndexChanged.connect(lambda _: self._refresh_list())
        toolbar.addWidget(self.filter_combo)

        self.count_label = create_muted_label("0 entries")
        toolbar.addWidget(self.count_label)
        toolbar.addStretch()

        open_log_btn = QPushButton("Open Log File")
        open_log_btn.setMinimumWidth(95)
        open_log_btn.setToolTip("Open the structured JSON log file")
        open_log_btn.clicked.connect(self._open_log_file)
        toolbar.addWidget(open_log_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setMinimumWidth(50)
        clear_btn.clicked.connect(self._clear)
        toolbar.addWidget(clear_btn)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.list_widget = QListWidget()
        self.list_widget.setFont(get_mono_font())
        self.list_widget.currentRowChanged.connect(self._show_detail)
        splitter.addWidget(self.list_widget)

        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setFont(get_mono_font())
        self.detail_text.setPlaceholderText("Select an entry above to see details")
        splitter.addWidget(self.detail_text)

        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)

        configure_splitter_persistence(
            splitter,
            settings_key=_SETTINGS_KEY_SPLITTER,
            default_sizes=_SPLITTER_DEFAULT_SIZES,
            settings=self._settings,
        )

        layout.addWidget(splitter, 1)

    # ── Public log methods ────────────────────────────────────────────────────

    def log_request(self, request) -> None:
        safe_url = redact_url(request.url) if request.url else ""
        entry = {
            "type": "request",
            "timestamp": utc_now().isoformat(timespec="milliseconds"),
            "method": request.method,
            "url": safe_url,
            "headers": redact_headers(dict(request.headers or {})),
            "params": dict(request.params or {}),
            "body": redact_body(request.body, max_length=_BODY_REDACT_LEN) if request.body else None,
        }
        _py_logger.debug(
            "→ %s %s", request.method, safe_url,
            extra={"method": request.method, "url": safe_url},
        )
        self._push(entry)

    def log_response(self, request, response) -> None:
        # Guard against None elapsed (e.g. cancelled or error responses).
        elapsed_ms = int((response.elapsed or 0) * 1000)
        safe_url = redact_url(request.url) if request.url else ""
        entry = {
            "type": "response",
            "timestamp": utc_now().isoformat(timespec="milliseconds"),
            "method": request.method,
            "url": safe_url,
            "status": response.status_code,
            "reason": response.reason,
            "elapsed_ms": elapsed_ms,
            "size_bytes": response.size,
            "headers": redact_headers(dict(response.headers or {})),
        }
        level = logging.INFO if response.status_code < 400 else logging.WARNING
        _py_logger.log(
            level,
            "← %d %s  %s  (%d ms)",
            response.status_code, response.reason, safe_url, elapsed_ms,
            extra={
                "method": request.method,
                "url": safe_url,
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
                "size_bytes": response.size,
            },
        )
        self._push(entry)

    def log_error(self, request, error) -> None:
        method = getattr(request, "method", "?")
        safe_url = redact_url(getattr(request, "url", "?"))
        safe_error = redact_body(str(error), max_length=_ERROR_REDACT_LEN) or str(error)
        entry = {
            "type": "error",
            "timestamp": utc_now().isoformat(timespec="milliseconds"),
            "method": method,
            "url": safe_url,
            "error": safe_error,
        }
        _py_logger.warning(
            "✗ %s %s — %s", method, safe_url, safe_error,
            extra={
                "method": method,
                "url": safe_url,
                "error_type": type(error).__name__,
            },
        )
        self._push(entry)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _push(self, entry: dict[str, Any]) -> None:
        """Append *entry* to storage, evict the oldest if at cap, update list.

        Entry dicts are stored directly in each ``QListWidgetItem`` (not an
        integer index), so eviction needs only a single O(k) scan to remove
        the one affected list item rather than rebuilding the entire list.
        """
        self._entries.append(entry)

        evicted: dict[str, Any] | None = None
        if len(self._entries) > MAX_LOG_ENTRIES:
            evicted = self._entries.pop(0)

        if self._passes_filter(entry):
            self._append_list_item(entry)

        # Remove the evicted entry from the list if it was visible.
        if evicted is not None:
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) is evicted:
                    self.list_widget.takeItem(i)
                    break

        self.count_label.setText(f"{len(self._entries)} entries")

    def _passes_filter(self, entry: dict[str, Any]) -> bool:
        f = self.filter_combo.currentText()
        if f == "All":
            return True
        expected = _FILTER_MAP.get(f)
        return expected is not None and entry["type"] == expected

    def _append_list_item(self, entry: dict[str, Any]) -> None:
        ts = entry["timestamp"][_TS_SLICE]
        text, color = self._entry_display(entry)
        item = QListWidgetItem(f"{ts}  {text}")
        item.setForeground(color)
        # Store the entry dict directly — no stale-index problem on eviction.
        item.setData(Qt.ItemDataRole.UserRole, entry)
        self.list_widget.addItem(item)
        self.list_widget.scrollToBottom()

    def _refresh_list(self) -> None:
        """Rebuild the list according to the current filter.

        Signals and screen updates are suppressed during the rebuild to avoid
        spurious ``currentRowChanged`` firings and per-item repaint overhead.
        """
        self.list_widget.blockSignals(True)
        self.list_widget.setUpdatesEnabled(False)
        try:
            self.list_widget.clear()
            for entry in self._entries:
                if self._passes_filter(entry):
                    self._append_list_item(entry)
        finally:
            self.list_widget.setUpdatesEnabled(True)
            self.list_widget.blockSignals(False)
        self.detail_text.clear()
        self.count_label.setText(f"{len(self._entries)} entries")

    def _show_detail(self, _row: int) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry is None:
            return
        try:
            text = json.dumps(entry, indent=2, default=str)
        except Exception:
            text = str(entry)
        self.detail_text.setPlainText(text)

    def _clear(self) -> None:
        self._entries.clear()
        self.list_widget.clear()
        self.detail_text.clear()
        self.count_label.setText("0 entries")

    def _open_log_file(self) -> None:
        """Open the structured log file in the OS default text viewer."""
        show_log_file_open_result(
            self,
            try_open_current_log_file(),
            "No log file found yet — send a request first to generate entries.",
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _entry_display(entry: dict[str, Any]) -> tuple[str, QColor]:
        """Return the list-item display text and foreground colour for *entry*."""
        t = entry["type"]
        if t == "request":
            return (
                f"→ {entry['method']:<7} {entry['url']}",
                QColor(Colors.INFO),
            )
        if t == "response":
            status = entry["status"]
            color = QColor(Colors.SUCCESS) if status < 400 else QColor(Colors.ERROR)
            return (
                f"← {status} {entry['reason']:<10} {entry['url']}  ({entry['elapsed_ms']} ms)",
                color,
            )
        # error
        return (
            f"✗ ERROR  {entry['url']}  {entry['error'][:_ERROR_PREVIEW_LEN]}",
            QColor(Colors.ERROR),
        )

