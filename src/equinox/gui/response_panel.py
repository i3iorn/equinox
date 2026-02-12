"""Response viewer panel"""

import json
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from equinox.core.request import Response


class ResponsePanel(QWidget):
    """Panel for displaying HTTP responses"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_response = None
        self._init_ui()

    def _init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self)

        # Status bar
        status_layout = QHBoxLayout()
        self.status_label = QLabel("No response")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.time_label = QLabel("")
        self.size_label = QLabel("")

        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        status_layout.addWidget(self.time_label)
        status_layout.addWidget(QLabel("|"))
        status_layout.addWidget(self.size_label)

        layout.addLayout(status_layout)

        # Tabs
        self.tabs = QTabWidget()

        # Body tab
        self.body_text = QTextEdit()
        self.body_text.setReadOnly(True)
        self.body_text.setFont(QFont("Courier New", 10))
        self.tabs.addTab(self.body_text, "Body")

        # Headers tab
        self.headers_table = QTableWidget()
        self.headers_table.setColumnCount(2)
        self.headers_table.setHorizontalHeaderLabels(["Header", "Value"])
        self.headers_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.headers_table, "Headers")

        layout.addWidget(self.tabs)

    def display_response(self, response: Response):
        """Display HTTP response"""
        self.current_response = response

        # Update status
        status_color = "green" if response.status_code < 400 else "red"
        self.status_label.setText(f"HTTP {response.status_code} {response.reason}")
        self.status_label.setStyleSheet(
            f"font-weight: bold; font-size: 14px; color: {status_color};"
        )

        self.time_label.setText(f"Time: {response.elapsed:.3f}s")
        self.size_label.setText(f"Size: {self._format_size(response.size)}")

        # Display body
        if response.is_json:
            try:
                data = response.json()
                formatted = json.dumps(data, indent=2)
                self.body_text.setPlainText(formatted)
            except:
                self.body_text.setPlainText(response.text)
        else:
            self.body_text.setPlainText(response.text)

        # Display headers
        self.headers_table.setRowCount(len(response.headers))
        for row, (key, value) in enumerate(response.headers.items()):
            self.headers_table.setItem(row, 0, QTableWidgetItem(key))
            self.headers_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def _format_size(self, size_bytes: int) -> str:
        """Format size in human-readable format"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def clear(self):
        """Clear response display"""
        self.status_label.setText("No response")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.time_label.clear()
        self.size_label.clear()
        self.body_text.clear()
        self.headers_table.setRowCount(0)
        self.current_response = None
