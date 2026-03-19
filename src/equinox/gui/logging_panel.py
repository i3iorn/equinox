"""Request/Response logging panel."""

import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QComboBox, QLabel, QSplitter,
    QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QFont, QColor

from equinox.core import utc_now
from equinox.gui.theme import Colors, get_mono_font
from equinox.core.redact import redact_headers

# Python logger — routes GUI traffic to the structured log file as well
_py_logger = logging.getLogger("equinox.gui.traffic")

MAX_LOG_ENTRIES = 500


class LoggingPanel(QWidget):
    """Sidebar panel showing a live log of all HTTP transactions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: List[Dict[str, Any]] = []
        self._settings = QSettings("Equinox", "Equinox")
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Toolbar
        toolbar = QHBoxLayout()
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Requests", "Responses", "Errors"])
        self.filter_combo.currentIndexChanged.connect(self._refresh_list)
        toolbar.addWidget(self.filter_combo)

        self.count_label = QLabel("0 entries")
        self.count_label.setObjectName("mutedLabel")
        toolbar.addWidget(self.count_label)
        toolbar.addStretch()

        open_log_btn = QPushButton("Open Log File")
        open_log_btn.setFixedWidth(95)
        open_log_btn.setToolTip("Open the structured JSON log file")
        open_log_btn.clicked.connect(self._open_log_file)
        toolbar.addWidget(open_log_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(50)
        clear_btn.clicked.connect(self._clear)
        toolbar.addWidget(clear_btn)
        layout.addLayout(toolbar)

        # Splitter: list on top, detail below
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

        # Restore saved splitter position (#1)
        saved = self._settings.value("splitter/logging")
        if saved:
            try:
                splitter.setSizes([int(x) for x in saved])
            except Exception:
                splitter.setSizes([200, 200])
        else:
            splitter.setSizes([200, 200])
        splitter.splitterMoved.connect(
            lambda: self._settings.setValue("splitter/logging", splitter.sizes())
        )

        layout.addWidget(splitter, 1)

    # ── Public log methods ────────────────────────────────────────────

    def log_request(self, request) -> None:
        entry = {
            "type": "request",
            "timestamp": utc_now().isoformat(timespec="milliseconds"),
            "method": request.method,
            "url": request.url,
            "headers": dict(request.headers or {}),
            "params": dict(request.params or {}),
            "body": request.body if request.body else None,
        }
        _py_logger.debug(
            "→ %s %s", request.method, request.url,
            extra={"method": request.method, "url": request.url},
        )
        self._push(entry)

    def log_response(self, request, response) -> None:
        elapsed_ms = int(response.elapsed * 1000)
        entry = {
            "type": "response",
            "timestamp": utc_now().isoformat(timespec="milliseconds"),
            "method": request.method,
            "url": request.url,
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
            response.status_code, response.reason, request.url, elapsed_ms,
            extra={
                "method": request.method,
                "url": request.url,
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
                "size_bytes": response.size,
            },
        )
        self._push(entry)

    def log_error(self, request, error) -> None:
        entry = {
            "type": "error",
            "timestamp": utc_now().isoformat(timespec="milliseconds"),
            "method": getattr(request, "method", "?"),
            "url": getattr(request, "url", "?"),
            "error": str(error),
        }
        _py_logger.error(
            "✗ %s %s — %s",
            getattr(request, "method", "?"),
            getattr(request, "url", "?"),
            error,
            extra={
                "method": getattr(request, "method", "?"),
                "url": getattr(request, "url", "?"),
                "error_type": type(error).__name__,
            },
        )
        self._push(entry)

    # ── Internal ──────────────────────────────────────────────────────

    def _push(self, entry: Dict[str, Any]) -> None:
        """Store entry and add to list if it passes the current filter."""
        self._entries.append(entry)
        # Evict oldest entries when over cap
        if len(self._entries) > MAX_LOG_ENTRIES:
            self._entries = self._entries[-MAX_LOG_ENTRIES:]
            self._refresh_list()
            return

        if self._passes_filter(entry):
            self._append_list_item(entry, len(self._entries) - 1)
        self.count_label.setText(f"{len(self._entries)} entries")

    def _passes_filter(self, entry: Dict[str, Any]) -> bool:
        f = self.filter_combo.currentText()
        if f == "All":
            return True
        return (
            (f == "Requests"  and entry["type"] == "request") or
            (f == "Responses" and entry["type"] == "response") or
            (f == "Errors"    and entry["type"] == "error")
        )

    def _append_list_item(self, entry: Dict[str, Any], index: int) -> None:
        t = entry["type"]
        ts = entry["timestamp"][11:23]   # HH:MM:SS.mmm

        if t == "request":
            text  = f"→ {entry['method']:<7} {entry['url']}"
            color = QColor(Colors.BLUE)
        elif t == "response":
            status = entry["status"]
            color  = QColor(Colors.GREEN) if status < 400 else QColor(Colors.RED)
            text   = f"← {status} {entry['reason']:<10} {entry['url']}  ({entry['elapsed_ms']} ms)"
        else:
            text  = f"✗ ERROR  {entry['url']}  {entry['error'][:60]}"
            color = QColor(Colors.RED)

        item = QListWidgetItem(f"{ts}  {text}")
        item.setForeground(color)
        item.setData(Qt.ItemDataRole.UserRole, index)
        self.list_widget.addItem(item)
        self.list_widget.scrollToBottom()

    def _refresh_list(self) -> None:
        """Rebuild the list according to the current filter."""
        self.list_widget.clear()
        for i, entry in enumerate(self._entries):
            if self._passes_filter(entry):
                self._append_list_item(entry, i)
        self.count_label.setText(f"{len(self._entries)} entries")

    def _show_detail(self, _row: int) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None or idx >= len(self._entries):
            return
        entry = self._entries[idx]
        self.detail_text.setPlainText(
            json.dumps(entry, indent=2, default=str)
        )

    def _clear(self) -> None:
        self._entries.clear()
        self.list_widget.clear()
        self.detail_text.clear()
        self.count_label.setText("0 entries")

    def _open_log_file(self) -> None:
        """Open the structured log file in the OS default text viewer."""
        import os, subprocess
        from equinox.core.log_setup import get_log_file
        from PyQt6.QtWidgets import QMessageBox

        log_path = get_log_file()
        if not log_path or not log_path.exists():
            QMessageBox.information(
                self, "Log File",
                "No log file found yet — send a request first to generate entries."
            )
            return
        try:
            if os.name == "nt":
                os.startfile(str(log_path))
            elif os.path.exists("/usr/bin/open"):   # macOS
                subprocess.Popen(["open", str(log_path)])
            else:
                subprocess.Popen(["xdg-open", str(log_path)])
        except Exception as exc:
            QMessageBox.information(
                self, "Log File",
                f"Log file:\n{log_path}\n\n(Could not open automatically: {exc})"
            )

