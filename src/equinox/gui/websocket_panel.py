"""WebSocket panel — connect, send, and receive WebSocket messages."""

from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit, QCheckBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from equinox.gui.theme import Colors


class _WSThread(QThread):
    """Background thread that owns the asyncio event loop + websocket connection."""

    message_received = pyqtSignal(str, str)   # (direction, text): "in" | "out"
    connected        = pyqtSignal()
    disconnected     = pyqtSignal()
    error_occurred   = pyqtSignal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url  = url
        self._loop = None
        self._ws   = None

    def run(self):
        import asyncio
        import sys
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect())
        finally:
            self._loop.close()

    async def _connect(self):
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
            self.error_occurred.emit(str(exc))
        finally:
            self._ws = None
            self.disconnected.emit()

    def send(self, text: str) -> None:
        if self._ws is not None and self._loop is not None:
            import asyncio
            asyncio.run_coroutine_threadsafe(self._ws.send(text), self._loop)

    def stop(self) -> None:
        if self._ws is not None and self._loop is not None:
            import asyncio
            asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)


class WebSocketPanel(QWidget):
    """Left-panel tab for WebSocket connections."""

    _COL_DIR  = 0
    _COL_TIME = 1
    _COL_SIZE = 2
    _COL_MSG  = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: _WSThread | None = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # URL + connect row
        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("wss://echo.websocket.org")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connection)
        url_row.addWidget(self.url_input, 3)
        url_row.addWidget(self.connect_btn)
        layout.addLayout(url_row)

        # Status
        self.status_label = QLabel("Disconnected")
        self.status_label.setObjectName("mutedLabel")
        layout.addWidget(self.status_label)

        # Log toolbar: Format JSON toggle + Clear
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

        # Message log — 4-column QTableWidget
        self.message_log = QTableWidget(0, 4)
        self.message_log.setHorizontalHeaderLabels(["", "Time", "Bytes", "Message"])
        hdr = self.message_log.horizontalHeader()
        hdr.setSectionResizeMode(self._COL_DIR,  QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(self._COL_TIME, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_MSG,  QHeaderView.ResizeMode.Stretch)
        self.message_log.setColumnWidth(self._COL_DIR, 26)
        self.message_log.verticalHeader().setVisible(False)
        self.message_log.setAlternatingRowColors(True)
        self.message_log.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.message_log.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.message_log.setWordWrap(False)
        layout.addWidget(self.message_log, 3)

        # Send row
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

    # ── Connection lifecycle ───────────────────────────────────────────

    def _toggle_connection(self):
        if self._thread is not None and self._thread.isRunning():
            self._thread.stop()
            self.connect_btn.setEnabled(False)
        else:
            url = self.url_input.text().strip()
            if not url:
                return
            self._thread = _WSThread(url, self)
            self._thread.connected.connect(self._on_connected)
            self._thread.disconnected.connect(self._on_disconnected)
            self._thread.message_received.connect(self._on_message)
            self._thread.error_occurred.connect(self._on_error)
            self._thread.start()
            self.connect_btn.setText("Disconnect")
            self.status_label.setText("Connecting…")
            self.status_label.setStyleSheet("")

    def _on_connected(self):
        self.status_label.setText("Connected")
        self.status_label.setStyleSheet(f"color: {Colors.GREEN};")
        self.send_btn.setEnabled(True)
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Disconnect")

    def _on_disconnected(self):
        self.status_label.setText("Disconnected")
        self.status_label.setStyleSheet("")
        self.connect_btn.setText("Connect")
        self.connect_btn.setEnabled(True)
        self.send_btn.setEnabled(False)
        self._thread = None

    def _on_error(self, msg: str):
        self._append_row("⚠", datetime.now().strftime("%H:%M:%S"), "—", f"Error: {msg}",
                         fg=Colors.RED)

    # ── Messaging ─────────────────────────────────────────────────────

    def _send_message(self):
        text = self.message_input.toPlainText()
        if not text or self._thread is None:
            return
        self._thread.send(text)
        self._on_message("out", text)
        self.message_input.clear()

    def _on_message(self, direction: str, text: str):
        arrow = "←" if direction == "in" else "→"
        ts    = datetime.now().strftime("%H:%M:%S")
        size  = f"{len(text.encode('utf-8'))} B"
        fg    = Colors.GREEN if direction == "in" else Colors.AMBER

        display = text
        if self._fmt_json_check.isChecked():
            try:
                import json as _json
                display = _json.dumps(_json.loads(text), indent=2, ensure_ascii=False)
            except Exception:
                pass

        self._append_row(arrow, ts, size, display, fg=fg)

    def _append_row(self, arrow: str, ts: str, size: str, msg: str, fg: str = "") -> None:
        row = self.message_log.rowCount()
        self.message_log.insertRow(row)

        dir_item  = QTableWidgetItem(arrow)
        time_item = QTableWidgetItem(ts)
        size_item = QTableWidgetItem(size)
        msg_item  = QTableWidgetItem(msg)

        for item in (dir_item, time_item, size_item, msg_item):
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if fg:
                item.setForeground(QColor(fg))

        self.message_log.setItem(row, self._COL_DIR,  dir_item)
        self.message_log.setItem(row, self._COL_TIME, time_item)
        self.message_log.setItem(row, self._COL_SIZE, size_item)
        self.message_log.setItem(row, self._COL_MSG,  msg_item)
        self.message_log.scrollToBottom()

    def _clear_log(self) -> None:
        self.message_log.setRowCount(0)

    # ── Cleanup ───────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._thread is not None and self._thread.isRunning():
            self._thread.stop()
            self._thread.wait(2000)
        super().closeEvent(event)
