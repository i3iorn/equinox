"""Request builder panel"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QLabel,
    QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt, QThread
from PyQt6.QtGui import QFont

from equinox.core.client import HTTPClient
from equinox.core.request import Request, Response
from equinox.core.exceptions import RequestError
from equinox.storage import Database, HistoryManager


class RequestWorker(QThread):
    """Worker thread for sending requests"""

    finished = pyqtSignal(object)  # Response or Exception
    progress = pyqtSignal(str)

    def __init__(self, request: Request):
        super().__init__()
        self.request = request

    def run(self):
        """Execute request in background"""
        try:
            self.progress.emit("Sending request...")
            client = HTTPClient()
            response = client.send(self.request)
            self.finished.emit(response)
        except Exception as e:
            self.finished.emit(e)


class RequestPanel(QWidget):
    """Panel for building and sending requests"""

    response_received = pyqtSignal(object)  # Response object

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.current_request = None
        self._init_ui()

    def _init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self)

        # Request line (method + URL + send button)
        request_line = QHBoxLayout()

        self.method_combo = QComboBox()
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_combo.setMaximumWidth(100)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter request URL...")
        self.url_input.returnPressed.connect(self._send_request)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._send_request)
        self.send_button.setMaximumWidth(100)

        request_line.addWidget(self.method_combo)
        request_line.addWidget(self.url_input)
        request_line.addWidget(self.send_button)

        layout.addLayout(request_line)

        # Tabs for request details
        self.tabs = QTabWidget()

        # Headers tab
        self.headers_table = self._create_key_value_table()
        self.tabs.addTab(self.headers_table, "Headers")

        # Query params tab
        self.params_table = self._create_key_value_table()
        self.tabs.addTab(self.params_table, "Params")

        # Body tab
        body_widget = QWidget()
        body_layout = QVBoxLayout(body_widget)
        self.body_text = QTextEdit()
        self.body_text.setPlaceholderText("Request body (JSON, XML, or plain text)")
        body_layout.addWidget(self.body_text)
        self.tabs.addTab(body_widget, "Body")

        # Auth tab
        auth_widget = QWidget()
        auth_layout = QVBoxLayout(auth_widget)
        auth_label = QLabel("Authentication support coming soon...")
        auth_layout.addWidget(auth_label)
        auth_layout.addStretch()
        self.tabs.addTab(auth_widget, "Auth")

        layout.addWidget(self.tabs)

        # Save button
        save_layout = QHBoxLayout()
        save_layout.addStretch()
        self.save_button = QPushButton("Save Request")
        self.save_button.clicked.connect(self._save_request)
        save_layout.addWidget(self.save_button)
        layout.addLayout(save_layout)

    def _create_key_value_table(self):
        """Create table for key-value pairs"""
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Key", "Value", ""])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(2, 30)
        table.setRowCount(5)  # Start with 5 empty rows
        return table

    def _get_table_data(self, table: QTableWidget) -> dict:
        """Extract key-value pairs from table"""
        data = {}
        for row in range(table.rowCount()):
            key_item = table.item(row, 0)
            value_item = table.item(row, 1)
            if key_item and value_item:
                key = key_item.text().strip()
                value = value_item.text().strip()
                if key and value:
                    data[key] = value
        return data

    def _send_request(self):
        """Send HTTP request"""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "Please enter a URL")
            return

        # Build request
        method = self.method_combo.currentText()
        headers = self._get_table_data(self.headers_table)
        params = self._get_table_data(self.params_table)
        body = self.body_text.toPlainText().strip() or None

        request = Request(
            method=method, url=url, headers=headers, params=params, body=body
        )

        self.current_request = request

        # Disable send button
        self.send_button.setEnabled(False)
        self.send_button.setText("Sending...")

        # Send in background thread
        self.worker = RequestWorker(request)
        self.worker.finished.connect(self._handle_response)
        self.worker.start()

    def _handle_response(self, result):
        """Handle request completion"""
        self.send_button.setEnabled(True)
        self.send_button.setText("Send")

        if isinstance(result, Exception):
            # Error occurred
            QMessageBox.critical(self, "Request Error", str(result))
            # Save error to history
            if self.current_request:
                history_mgr = HistoryManager(self.db)
                history_mgr.save_history(self.current_request, error=str(result))
        else:
            # Success
            response = result
            self.response_received.emit(response)

            # Save to history
            history_mgr = HistoryManager(self.db)
            history_mgr.save_history(self.current_request, response)

    def _save_request(self):
        """Save current request to collection"""
        from PyQt6.QtWidgets import QInputDialog
        from equinox.storage import CollectionManager

        # Get request name
        name, ok = QInputDialog.getText(self, "Save Request", "Request name:")
        if not ok or not name:
            return

        # Build request
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "Please enter a URL")
            return

        method = self.method_combo.currentText()
        headers = self._get_table_data(self.headers_table)
        params = self._get_table_data(self.params_table)
        body = self.body_text.toPlainText().strip() or None

        request = Request(
            method=method, url=url, headers=headers, params=params, body=body, name=name
        )

        # Save to database
        mgr = CollectionManager(self.db)
        req_id = mgr.save_request(request, name)

        QMessageBox.information(self, "Success", f"Request saved with ID: {req_id}")

    def load_request(self, request: Request):
        """Load request into panel"""
        self.url_input.setText(request.url)

        # Set method
        index = self.method_combo.findText(request.method)
        if index >= 0:
            self.method_combo.setCurrentIndex(index)

        # Load headers
        self._load_table_data(self.headers_table, request.headers)

        # Load params
        self._load_table_data(self.params_table, request.params)

        # Load body
        if request.body:
            self.body_text.setPlainText(request.body)
        else:
            self.body_text.clear()

        self.current_request = request

    def _load_table_data(self, table: QTableWidget, data: dict):
        """Load key-value pairs into table"""
        table.setRowCount(len(data) + 5)  # Extra rows for new entries
        row = 0
        for key, value in data.items():
            table.setItem(row, 0, QTableWidgetItem(key))
            table.setItem(row, 1, QTableWidgetItem(value))
            row += 1

    def clear(self):
        """Clear request panel"""
        self.url_input.clear()
        self.method_combo.setCurrentIndex(0)
        self.headers_table.setRowCount(5)
        self.headers_table.clearContents()
        self.params_table.setRowCount(5)
        self.params_table.clearContents()
        self.body_text.clear()
        self.current_request = None
