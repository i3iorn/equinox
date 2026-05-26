"""WebSocket panel — connect, send, and receive WebSocket messages."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime
from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from equinox.core.validation import Validator
from equinox.gui.theme import Colors

__all__ = ["WebSocketPanel"]

logger = logging.getLogger(__name__)

# Maximum rows kept in the message log before oldest rows are evicted.
MAX_LOG_ROWS = 1_000


# ── Background WebSocket thread ───────────────────────────────────────────────


class _WSThread(QThread):
    """Background thread that owns the asyncio event loop + WebSocket connection."""

    message_received = pyqtSignal(str, str)  # (direction, text): "in" | "out"
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, url: str) -> None:
        super().__init__()  # no Qt parent — lifetime managed via deleteLater
        self._url: str = url
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any | None = None  # websockets.WebSocketClientProtocol at runtime
        self._connect_task: asyncio.Task[None] | None = None

    # ── QThread entry point ───────────────────────────────────────────────────

    def run(self) -> None:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._connect_task = self._loop.create_task(self._connect())
            self._loop.run_until_complete(self._connect_task)
        except asyncio.CancelledError:
            logger.debug("WebSocket thread cancelled for %s", self._url)
        finally:
            self._connect_task = None
            self._loop.close()

    async def _connect(self) -> None:
        try:
            import websockets
        except ImportError:
            self.error_occurred.emit(
                "websockets package not installed — run: pip install 'websockets>=12.0'"
            )
            return
        try:
            async with websockets.connect(self._url) as ws:
                self._ws = ws
                self.connected.emit()
                async for msg in ws:
                    self.message_received.emit("in", str(msg))
        except Exception as exc:
            logger.warning("WebSocket error for %s: %s", self._url, exc)
            self.error_occurred.emit(str(exc))
        finally:
            self._ws = None
            self.disconnected.emit()

    # ── Thread-safe helpers ───────────────────────────────────────────────────

    def send(self, text: str) -> None:
        """Schedule a send on the asyncio loop from any thread."""
        if self._ws is None or self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._safe_send(text), self._loop)

    async def _safe_send(self, text: str) -> None:
        """Send *text*, emitting ``error_occurred`` on failure instead of raising."""
        try:
            if self._ws is not None:
                await self._ws.send(text)
        except Exception as exc:
            logger.debug("WebSocket send failed: %s", exc)
            self.error_occurred.emit(f"Send error: {exc}")

    def stop(self) -> None:
        """Request a clean close of the WebSocket from any thread."""
        if self._loop is None or self._loop.is_closed():
            return
        if self._connect_task is not None and not self._connect_task.done():
            self._loop.call_soon_threadsafe(self._connect_task.cancel)
        if self._ws is not None:
            asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)

    async def _close_ws(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.close()
        except Exception as exc:
            logger.debug("WebSocket close failed: %s", exc)


# ── Panel ─────────────────────────────────────────────────────────────────────


class WebSocketPanel(QWidget):
    """Left-panel tab for WebSocket connections."""

    _COL_DIR = 0
    _COL_TIME = 1
    _COL_SIZE = 2
    _COL_MSG = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thread: _WSThread | None = None
        self._init_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("wss://echo.websocket.org")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connection)
        url_row.addWidget(self.url_input, 3)
        url_row.addWidget(self.connect_btn)
        layout.addLayout(url_row)

        self.status_label = QLabel("Disconnected")
        self.status_label.setObjectName("mutedLabel")
        layout.addWidget(self.status_label)

        log_toolbar = QHBoxLayout()
        self._fmt_json_check = QCheckBox("Format JSON")
        self._fmt_json_check.setToolTip(
            "Pretty-print JSON messages in the log (applies to new messages only)"
        )
        log_toolbar.addWidget(self._fmt_json_check)
        log_toolbar.addStretch()
        self.clear_btn = QPushButton("Clear Log")
        self.clear_btn.clicked.connect(self._clear_log)
        log_toolbar.addWidget(self.clear_btn)
        layout.addLayout(log_toolbar)

        self.message_log = QTableWidget(0, 4)
        self.message_log.setHorizontalHeaderLabels(["", "Time", "Bytes", "Message"])
        hdr = self.message_log.horizontalHeader()
        if hdr is not None:
            hdr.setSectionResizeMode(self._COL_DIR, QHeaderView.ResizeMode.Fixed)
            hdr.setSectionResizeMode(self._COL_TIME, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(self._COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(self._COL_MSG, QHeaderView.ResizeMode.Stretch)
        self.message_log.setColumnWidth(self._COL_DIR, 26)
        v_header = self.message_log.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        self.message_log.setAlternatingRowColors(True)
        self.message_log.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.message_log.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.message_log.setWordWrap(False)
        layout.addWidget(self.message_log, 3)

        send_row = QHBoxLayout()
        self.message_input = QPlainTextEdit()
        self.message_input.setMaximumHeight(60)
        self.message_input.setPlaceholderText("Message to send…")
        self.send_btn = QPushButton("Send")
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send_message)
        send_row.addWidget(self.message_input, 3)
        send_row.addWidget(self.send_btn)
        layout.addLayout(send_row)

    # ── Connection lifecycle ──────────────────────────────────────────────────

    def _toggle_connection(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.stop()
            self.connect_btn.setEnabled(False)
            return

        url = self.url_input.text().strip()
        if not url:
            return
        try:
            Validator.validate_url(url)
        except Exception as exc:
            self.status_label.setText(str(exc))
            return

        self._thread = _WSThread(url)
        # Ensure the thread object is destroyed cleanly after run() returns,
        # even if self._thread is set to None while the thread is finishing.
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.connected.connect(self._on_connected)
        self._thread.disconnected.connect(self._on_disconnected)
        self._thread.message_received.connect(self._on_message)
        self._thread.error_occurred.connect(self._on_error)
        self._thread.start()
        self.connect_btn.setText("Disconnect")
        self.status_label.setText("Connecting…")

    def _on_connected(self) -> None:
        self.status_label.setText("Connected")
        self.send_btn.setEnabled(True)
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Disconnect")

    def _on_disconnected(self) -> None:
        self.status_label.setText("Disconnected")
        self.connect_btn.setText("Connect")
        self.connect_btn.setEnabled(True)
        self.send_btn.setEnabled(False)
        # Release our reference; the thread cleans itself up via deleteLater
        # (connected above at creation time) once run() returns.
        self._thread = None

    def _on_error(self, msg: str) -> None:
        logger.warning("WebSocket error: %s", msg)
        self._append_row(
            "⚠", datetime.now().strftime("%H:%M:%S"), "—", f"Error: {msg}", fg=Colors.RED
        )

    # ── Messaging ─────────────────────────────────────────────────────────────

    def _send_message(self) -> None:
        text = self.message_input.toPlainText()
        if not text or self._thread is None:
            return
        self._thread.send(text)
        self._on_message("out", text)
        self.message_input.clear()

    def _on_message(self, direction: str, text: str) -> None:
        arrow = "←" if direction == "in" else "→"
        ts = datetime.now().strftime("%H:%M:%S")
        size = f"{len(text.encode('utf-8'))} B"
        fg = Colors.GREEN if direction == "in" else Colors.AMBER

        display = text
        if self._fmt_json_check.isChecked():
            try:
                display = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except Exception:
                pass

        self._append_row(arrow, ts, size, display, fg=fg)

    def _append_row(self, arrow: str, ts: str, size: str, msg: str, fg: str = "") -> None:
        """Insert a new row into the message log, evicting the oldest if at cap.

        Screen updates are suppressed for the duration of the insert so a
        single repaint covers the row addition and the scroll-to-bottom.
        """
        self.message_log.setUpdatesEnabled(False)
        try:
            # Evict oldest row when at the cap to bound memory usage.
            if self.message_log.rowCount() >= MAX_LOG_ROWS:
                self.message_log.removeRow(0)

            row = self.message_log.rowCount()
            self.message_log.insertRow(row)

            for col, text in enumerate((arrow, ts, size, msg)):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if fg:
                    item.setForeground(QColor(fg))
                self.message_log.setItem(row, col, item)
        finally:
            self.message_log.setUpdatesEnabled(True)

        self.message_log.scrollToBottom()

    def _clear_log(self) -> None:
        self.message_log.setRowCount(0)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.stop()
            # Wait briefly so the asyncio loop can close cleanly.
            # We do NOT block longer than 1 s to avoid freezing the shutdown.
            self._thread.wait(1_000)
        super().closeEvent(event)
