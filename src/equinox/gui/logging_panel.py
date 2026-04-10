"""Request/Response logging panel."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QComboBox, QLabel, QSplitter,
    QListWidget, QListWidgetItem, QMessageBox,
)
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QColor

from equinox.core.log_setup import get_log_file
from equinox.core.redact import redact_body, redact_headers, redact_url
from equinox.core.time import utc_now
from equinox.gui.theme import Colors, get_mono_font

__all__ = ["LoggingPanel"]

# Routes GUI traffic to the structured log file as well as the panel.
_py_logger = logging.getLogger("equinox.gui.traffic")

MAX_LOG_ENTRIES = 500


class LoggingPanel(QWidget):
    """Sidebar panel showing a live log of all HTTP transactions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[dict[str, Any]] = []
        self._settings = QSettings("Equinox", "Equinox")
        self._init_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

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
            "body": redact_body(request.body, max_length=2000) if request.body else None,
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
        safe_url = redact_url(getattr(request, "url", "?"))
        safe_error = redact_body(str(error), max_length=500) or str(error)
        entry = {
            "type": "error",
            "timestamp": utc_now().isoformat(timespec="milliseconds"),
            "method": getattr(request, "method", "?"),
            "url": safe_url,
            "error": safe_error,
        }
        _py_logger.warning(
            "✗ %s %s — %s",
            getattr(request, "method", "?"),
            safe_url,
            safe_error,
            extra={
                "method": getattr(request, "method", "?"),
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
                if self.list_widget.item(i).data(Qt.ItemDataRole.UserRole) is evicted:
                    self.list_widget.takeItem(i)
                    break

        self.count_label.setText(f"{len(self._entries)} entries")

    def _passes_filter(self, entry: dict[str, Any]) -> bool:
        f = self.filter_combo.currentText()
        if f == "All":
            return True
        return (
            (f == "Requests"  and entry["type"] == "request")  or
            (f == "Responses" and entry["type"] == "response") or
            (f == "Errors"    and entry["type"] == "error")
        )

    def _append_list_item(self, entry: dict[str, Any]) -> None:
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
        log_path = get_log_file()
        if not log_path or not log_path.exists():
            QMessageBox.information(
                self, "Log File",
                "No log file found yet — send a request first to generate entries.",
            )
            return

        # Validate the resolved path before handing it to OS commands.
        resolved = log_path.resolve()
        if not str(resolved).endswith(".log"):
            return

        try:
            if sys.platform == "win32":
                os.startfile(str(resolved))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(resolved)])  # noqa: S603
            else:
                subprocess.Popen(["xdg-open", str(resolved)])  # noqa: S603
        except Exception as exc:
            QMessageBox.information(
                self, "Log File",
                f"Log file:\n{log_path}\n\n(Could not open automatically: {exc})",
            )

