"""OAuth2 form tab shared by the auth dialog and the credentials manager.

Field set is the union of the former request-dialog tab and the saved-
credentials OAuth page: both hosts now render exactly this form.
"""

import json
from typing import Any

from equinox.gui.dialogs._oauth_form_utils import parse_json_object_field
from equinox.gui.dialogs._oauth_form_utils import parse_json_object_field_lenient
from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.dialogs.auth_dialog.tabs.base import config_text
from equinox.gui.theme import get_mono_font
from equinox.gui.widgets import make_secret_row
from equinox.storage.oauth_clients import GRANT_TYPES
from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

_DEFAULT_GRANT_TYPE = "client_credentials"
_DEFAULT_TOKEN_FIELD = "access_token"
_DEFAULT_TOKEN_TTL = 3600


def _headers_text(headers: dict[str, str]) -> str:
    """Render a headers dict as ``"Name: Value, Name: Value"``."""
    return ", ".join(f"{name}: {value}" for name, value in headers.items())


def _parse_headers_text(raw: str) -> dict[str, str]:
    """Parse ``"Header: Value"`` pairs separated by commas into a dict."""
    headers: dict[str, str] = {}
    if not raw:
        return headers
    for pair in raw.split(","):
        name, _, value = pair.strip().partition(":")
        if name.strip() and value.strip():
            headers[name.strip()] = value.strip()
    return headers


class OAuth2AuthTab(AuthDialogTab, QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.token_url = QLineEdit()
        self.token_url.setPlaceholderText("https://auth.example.com/oauth/token")
        self.client_id = QLineEdit()
        self.client_id.setPlaceholderText("client_id")
        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_secret.setPlaceholderText("client_secret")
        self.scope = QLineEdit()
        self.scope.setPlaceholderText("read write  (optional)")

        self.token_auth = QComboBox()
        self.token_auth.addItem("Body (RFC 6749 default)", userData="body")
        self.token_auth.addItem("HTTP Basic Auth (D&B Direct+, GitHub…)", userData="basic")
        self.token_auth.setToolTip(
            "How client credentials are sent to the token endpoint.\n"
            "• Body — client_id/client_secret in the POST body (default, RFC 6749 §2.3.1)\n"
            "• Basic — Base64-encoded Authorization header (required by some providers)",
        )

        self.grant_type = QComboBox()
        self.grant_type.addItems(list(GRANT_TYPES))

        self.extra_params = QTextEdit()
        self.extra_params.setPlaceholderText('{ "audience": "https://api.example.com" }')
        self.extra_params.setMaximumHeight(70)
        self.extra_params.setFont(get_mono_font())

        self.token_field_name = QLineEdit(_DEFAULT_TOKEN_FIELD)
        self.token_field_name.setPlaceholderText(
            "JSON field holding the token (e.g. JWTToken)",
        )
        self.token_headers = QLineEdit()
        self.token_headers.setPlaceholderText(
            "Extra token-request headers, e.g. Authorization: Bearer key",
        )
        self.token_ttl = QLineEdit(str(_DEFAULT_TOKEN_TTL))
        self.token_ttl.setPlaceholderText(
            "Fallback TTL in seconds when response has no expires_in",
        )

        self.verify_ssl = QCheckBox("Verify SSL")
        self.verify_ssl.setChecked(True)

        self.access_token = QLineEdit()
        self.access_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.refresh_token = QLineEdit()
        self.refresh_token.setEchoMode(QLineEdit.EchoMode.Password)

        layout.addRow("Token URL:*", self.token_url)
        layout.addRow("Client ID:*", self.client_id)
        layout.addRow("Client Secret:", make_secret_row(self.client_secret))
        layout.addRow("Scope:", self.scope)
        layout.addRow("Client Auth:", self.token_auth)
        layout.addRow("Grant Type:", self.grant_type)
        layout.addRow("Extra Params:", self.extra_params)
        layout.addRow("Token Field:", self.token_field_name)
        layout.addRow("Token Headers:", self.token_headers)
        layout.addRow("Token TTL (s):", self.token_ttl)
        layout.addRow("", self.verify_ssl)
        layout.addRow("Access Token:", make_secret_row(self.access_token))
        layout.addRow("Refresh Token:", make_secret_row(self.refresh_token))

        # Fetch controls live in one container so hosts with their own
        # test/fetch button (the credentials manager) can hide the whole row
        # without unwiring anything.
        self.fetch_btn = QPushButton("Fetch Token…")
        self.view_btn = QPushButton("View Response…")
        self.view_btn.setEnabled(False)

        button_row = QHBoxLayout()
        button_row.addWidget(self.fetch_btn)
        button_row.addWidget(self.view_btn)
        button_row.addStretch()

        self.status = QLabel("")

        self.fetch_bar = QWidget()
        bar_layout = QVBoxLayout(self.fetch_bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.addLayout(button_row)
        bar_layout.addWidget(self.status)
        layout.addRow("", self.fetch_bar)

    def set_fetch_ui_visible(self, visible: bool) -> None:
        """Show or hide the inline Fetch Token…/View Response… controls."""
        self.fetch_bar.setVisible(visible)

    def _parse_token_ttl(self) -> int:
        try:
            return int(self.token_ttl.text().strip())
        except ValueError:
            return _DEFAULT_TOKEN_TTL

    def get_auth_config(self) -> dict[str, Any]:
        return {
            "token_url": self.token_url.text().strip(),
            "client_id": self.client_id.text().strip(),
            "client_secret": self.client_secret.text().strip() or None,
            "scope": self.scope.text().strip() or None,
            "token_auth": self.token_auth.currentData(),
            "grant_type": self.grant_type.currentText(),
            "extra_params": parse_json_object_field_lenient(self.extra_params.toPlainText()),
            "token_field_name": self.token_field_name.text().strip() or _DEFAULT_TOKEN_FIELD,
            "token_request_headers": _parse_headers_text(self.token_headers.text().strip()) or None,
            "default_token_ttl_seconds": self._parse_token_ttl(),
            "verify_ssl": self.verify_ssl.isChecked(),
            "access_token": self.access_token.text().strip() or None,
            "refresh_token": self.refresh_token.text().strip() or None,
        }

    def set_auth_config(self, config: dict[str, Any]) -> None:
        self.token_url.setText(config_text(config, "token_url"))
        self.client_id.setText(config_text(config, "client_id"))
        self.client_secret.setText(config_text(config, "client_secret"))
        self.scope.setText(config_text(config, "scope"))

        token_auth_index = self.token_auth.findData(config.get("token_auth") or "body")
        self.token_auth.setCurrentIndex(max(token_auth_index, 0))

        grant_index = self.grant_type.findText(
            config_text(config, "grant_type", _DEFAULT_GRANT_TYPE),
        )
        self.grant_type.setCurrentIndex(max(grant_index, 0))

        extra = config.get("extra_params")
        self.extra_params.setPlainText(
            json.dumps(extra, indent=2) if isinstance(extra, dict) and extra else "",
        )

        self.token_field_name.setText(
            config_text(config, "token_field_name", _DEFAULT_TOKEN_FIELD) or _DEFAULT_TOKEN_FIELD,
        )

        headers = config.get("token_request_headers")
        self.token_headers.setText(
            _headers_text(headers) if isinstance(headers, dict) and headers else "",
        )

        ttl = config.get("default_token_ttl_seconds")
        self.token_ttl.setText(str(ttl) if ttl else str(_DEFAULT_TOKEN_TTL))

        self.verify_ssl.setChecked(config.get("verify_ssl") is not False)
        self.access_token.setText(config_text(config, "access_token"))
        self.refresh_token.setText(config_text(config, "refresh_token"))

    def validate(self) -> str | None:
        if not self.token_url.text().strip():
            return "Token URL is required for OAuth 2.0."
        if not self.client_id.text().strip():
            return "Client ID is required for OAuth 2.0."
        _value, error = parse_json_object_field(self.extra_params.toPlainText())
        if error is not None:
            return error
        return None
