"""
Focused UI-builder mixins for `RequestPanel`.

`RequestPanelLayoutMixin` is the canonical owner of tab/body layout construction.
This module intentionally keeps only the URL bar mixin used by `RequestPanel`
and a lightweight orchestration shim for backward compatibility.
"""

from __future__ import annotations

from typing import Any

from equinox.gui.request_panel._constants import CANCEL_BTN_WIDTH
from equinox.gui.request_panel._constants import METHOD_COMBO_WIDTH
from equinox.gui.request_panel._constants import SEND_BTN_WIDTH
from equinox.gui.widgets import UrlLineEdit
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QToolButton


class RequestPanelOrchestrationMixin:
    """Backward-compatible shim for older call sites.

    `RequestPanel` now builds UI through `RequestPanelLayoutMixin._init_ui()`.
    """

    def build_request_panel_ui(self: Any) -> None:
        """Delegate to the canonical panel layout initializer."""
        self._init_ui()


class URLBarMixin:
    """Build the method/URL/send/cancel row."""

    url_input: UrlLineEdit
    _url_fix_suggestion: str | None

    def build_url_bar(self: Any) -> QHBoxLayout:
        """Create the request URL control row."""
        row = QHBoxLayout()
        row.setSpacing(4)

        self.method_combo = QComboBox()
        self.method_combo.setObjectName("requestMethodCombo")
        self.method_combo.setProperty("usage_track_id", "request.method_combo")
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_combo.setFixedWidth(METHOD_COMBO_WIDTH)

        self.url_input = UrlLineEdit()
        self.url_input.setPlaceholderText(
            "https://api.example.com/v1/resource  -  {{VAR}} for variables  -  Ctrl+N = new",
        )
        self.url_input.returnPressed.connect(self._send_request)

        self._url_fix_button = QToolButton()
        self._url_fix_button.setText("Fix URL")
        self._url_fix_button.setToolTip("Apply suggested URL fix")
        self._url_fix_button.clicked.connect(self._apply_url_fix)
        self._url_fix_button.setVisible(False)
        self._url_fix_suggestion = None

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("sendBtn")
        self.send_button.setProperty("usage_track_id", "request.send")
        self.send_button.setMinimumWidth(SEND_BTN_WIDTH)
        self.send_button.setToolTip("Send request (Ctrl+Enter)")
        self.send_button.clicked.connect(self._send_request)
        self.send_button.setDefault(True)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelBtn")
        self.cancel_button.setProperty("usage_track_id", "request.cancel")
        self.cancel_button.setMinimumWidth(CANCEL_BTN_WIDTH)
        self.cancel_button.setToolTip("Cancel the in-flight request")
        self.cancel_button.clicked.connect(self._cancel_request)
        self.cancel_button.setVisible(False)

        row.addWidget(self.method_combo)
        row.addWidget(self.url_input, 1)
        row.addWidget(self._url_fix_button)
        row.addWidget(self.send_button)
        row.addWidget(self.cancel_button)

        return row
