"""Saved credentials manager dialog.

Manages named, reusable auth credentials of any supported type
(OAuth 2.0, API Key, Basic Auth, Bearer Token).  Replaces the OAuth2-only
OAuthClientsDialog as the primary credential manager opened from AuthDialog.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6.QtGui import QFont, QColor

from equinox.auth import AUTH_TYPES
from equinox.gui.dialogs._oauth_connection_test_mixin import OAuthConnectionTestMixin
from equinox.gui.dialogs._oauth_form_utils import (
    parse_json_object_field,
)
from equinox.gui.theme import Colors
from equinox.gui.theme import get_mono_font
from equinox.gui.widgets.secret_row import make_secret_row as _secret_row
from equinox.gui.workers import OAuthTokenTester
from equinox.storage import Database
from equinox.storage.oauth_clients import GRANT_TYPES
from equinox.storage.saved_credentials import SavedCredentialsManager
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QListWidgetItem
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QDialogButtonBox
from PyQt6.QtWidgets import QFormLayout
from PyQt6.QtWidgets import QFrame
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QInputDialog
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QListWidget
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QSplitter
from PyQt6.QtWidgets import QStackedWidget
from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

# HTML fragment shown in the form header when no credential is loaded.
_FORM_HEADER_IDLE = (
    f"<b>Credential Details</b>"
    f"<span style='color:{Colors.FG_MUTED};'>  (select a credential)</span>"
)


# Colour per auth type used in the list widget
_TYPE_COLOUR = {
    "oauth2": Colors.BLUE,
    "api_key": Colors.AMBER,
    "basic": Colors.GREEN,
    "bearer": Colors.PURPLE,
    "aws_sigv4": Colors.RED,
}

# Stack page order.  Declared explicitly rather than relying on ``AUTH_TYPES``
# iteration order matching the order pages are added to the QStackedWidget.
_PAGE_ORDER = ("oauth2", "api_key", "basic", "bearer", "aws_sigv4")


def _text(cfg: dict[str, Any], key: str, default: str = "") -> str:
    """Read a config value as text.

    Optional fields are persisted as ``None`` rather than omitted, and
    ``QLineEdit.setText(None)`` raises, so ``None`` must fall back too.
    """
    value = cfg.get(key)
    return default if value is None else str(value)


def _hline() -> QFrame:
    """A horizontal rule used to separate form sections."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    return line


@dataclass(frozen=True)
class CredentialConfig:
    auth_type: str
    name: str
    description: str
    config: dict[str, Any]


@dataclass(frozen=True)
class TestResult:
    ok: bool
    message: str
    response: dict[str, Any] | None = None


@dataclass(frozen=True)
class AuthConfigResult:
    config: dict[str, Any] | None
    error: str | None


class AuthConfigCollector:
    """Collects and validates authentication configuration from GUI widgets."""

    def __init__(self, dialog: Any) -> None:
        # Dependency injection: dialog supplies the widgets
        self._d = dialog

    def collect(self, auth_type: str) -> AuthConfigResult:
        handlers: dict[str, Callable[[], AuthConfigResult]] = {
            "oauth2": self._collect_oauth2,
            "api_key": self._collect_api_key,
            "basic": self._collect_basic,
            "bearer": self._collect_bearer,
            "aws_sigv4": self._collect_aws_sigv4,
        }
        handler = handlers.get(auth_type)
        if handler is None:
            return AuthConfigResult(None, f"Unsupported authentication type: {auth_type}")
        return handler()

    # --- OAuth2 -------------------------------------------------------

    def _collect_oauth2(self) -> AuthConfigResult:
        d = self._d
        token_url = d.o_token_url.text().strip()
        client_id = d.o_client_id.text().strip()

        if not token_url:
            return AuthConfigResult(None, "Token URL is required for OAuth 2.0.")
        if not client_id:
            return AuthConfigResult(None, "Client ID is required for OAuth 2.0.")

        extra_params, error = parse_json_object_field(d.o_extra.toPlainText())
        if extra_params is None:
            return AuthConfigResult(None, error)

        return AuthConfigResult(
            {
                "token_url": token_url,
                "client_id": client_id,
                "client_secret": d.o_client_secret.text() or None,
                "scope": d.o_scope.text().strip() or None,
                "token_auth": d.o_token_auth.currentData() or "body",
                "grant_type": d.o_grant_type.currentText(),
                "extra_params": extra_params,
            },
            None,
        )

    # --- API Key ------------------------------------------------------

    def _collect_api_key(self) -> AuthConfigResult:
        d = self._d
        key = d.ak_key.text().strip()
        value = d.ak_value.text()

        if not key:
            return AuthConfigResult(None, "Header/Param Name is required for API Key.")
        if not value:
            return AuthConfigResult(None, "Key Value is required for API Key.")

        return AuthConfigResult(
            {
                "key": key,
                "value": value,
                "location": d.ak_location.currentText(),
            },
            None,
        )

    # --- Basic --------------------------------------------------------

    def _collect_basic(self) -> AuthConfigResult:
        d = self._d
        username = d.ba_username.text().strip()
        password = d.ba_password.text()

        if not username:
            return AuthConfigResult(None, "Username is required for Basic Auth.")
        if not password:
            return AuthConfigResult(None, "Password is required for Basic Auth.")

        return AuthConfigResult({"username": username, "password": password}, None)

    # --- Bearer -------------------------------------------------------

    def _collect_bearer(self) -> AuthConfigResult:
        d = self._d
        token = d.bt_token.text().strip()
        if not token:
            return AuthConfigResult(None, "Token is required for Bearer Token.")
        return AuthConfigResult({"token": token}, None)

    # --- AWS SigV4 ----------------------------------------------------

    def _collect_aws_sigv4(self) -> AuthConfigResult:
        d = self._d
        access_key = d.aws_access_key.text().strip()
        secret_key = d.aws_secret_key.text().strip()
        region = d.aws_region.text().strip()
        service = d.aws_service.text().strip()

        if not access_key:
            return AuthConfigResult(None, "Access Key ID is required for AWS SigV4.")
        if not secret_key:
            return AuthConfigResult(None, "Secret Access Key is required for AWS SigV4.")
        if not region:
            return AuthConfigResult(None, "Region is required for AWS SigV4.")
        if not service:
            return AuthConfigResult(None, "Service is required for AWS SigV4.")

        cfg = {
            "access_key": access_key,
            "secret_key": secret_key,
            "region": region,
            "service": service,
        }

        session_token = d.aws_session_token.text().strip()
        if session_token:
            cfg["session_token"] = session_token

        return AuthConfigResult(cfg, None)


class SavedCredentialsService:
    """Business logic for saved credentials."""

    def __init__(
        self,
        db: Database,
        manager: SavedCredentialsManager | None = None,
        config_collector: AuthConfigCollector | None = None,
    ) -> None:
        self._db = db
        self._mgr = manager or SavedCredentialsManager(db)
        self._collector = config_collector

    @property
    def manager(self) -> SavedCredentialsManager:
        return self._mgr

    def list_credentials(self) -> list[dict[str, Any]]:
        return self._mgr.list()

    def get_credential(self, cred_id: int) -> dict[str, Any] | None:
        return self._mgr.get(cred_id)

    def create_credential(self, name: str, auth_type: str = "oauth2") -> int:
        return self._mgr.create(name=name, auth_type=auth_type)

    def duplicate_credential(self, cred_id: int, new_name: str) -> int:
        return self._mgr.duplicate(cred_id, new_name=new_name)

    def delete_credential(self, cred_id: int) -> None:
        self._mgr.delete(cred_id)

    def set_default(self, cred_id: int) -> None:
        self._mgr.set_default(cred_id)

    def bind_collector(self, collector: AuthConfigCollector) -> None:
        """Attach the widget collector once a view exists to read from."""
        self._collector = collector

    def collect_config(self, auth_type: str) -> AuthConfigResult:
        if self._collector is None:
            return AuthConfigResult(None, "Internal error: no config collector.")
        return self._collector.collect(auth_type)

    def update_credential(self, cred_id: int, data: CredentialConfig) -> None:
        self._mgr.update(
            cred_id,
            name=data.name,
            auth_type=data.auth_type,
            config=data.config,
            description=data.description,
        )


class SavedCredentialsView(QDialog):
    """View for managing saved credentials."""

    credentials_changed = pyqtSignal()
    save_requested = pyqtSignal()
    delete_requested = pyqtSignal()
    duplicate_requested = pyqtSignal()
    new_requested = pyqtSignal()
    set_default_requested = pyqtSignal()
    test_requested = pyqtSignal()
    view_response_requested = pyqtSignal()
    selection_changed = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_id: int | None = None
        self._dirty = False
        self._last_test_response: dict[str, Any] | None = None

        self.setWindowTitle("Saved Credentials")
        self.setMinimumSize(960, 580)
        self._build_ui()
        self._list_widget = self.cred_list

    # --- UI construction ----------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([240, 720])
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        root.addWidget(splitter, 1)

        close_btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btns.rejected.connect(self.close)
        root.addWidget(close_btns)

        self._set_form_enabled(False)

    def _build_left_panel(self) -> QWidget:
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(QLabel("<b>Saved Credentials</b>"))

        self.cred_list = QListWidget()
        self.cred_list.setAlternatingRowColors(True)
        self.cred_list.currentItemChanged.connect(self._on_item_selected)
        ll.addWidget(self.cred_list, 1)

        list_btns = QHBoxLayout()
        self.new_btn = QPushButton("New…")
        self.dup_btn = QPushButton("Duplicate")
        self.delete_btn = QPushButton("Delete")
        self.dup_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        for b in (self.new_btn, self.dup_btn, self.delete_btn):
            list_btns.addWidget(b)
        list_btns.addStretch()
        ll.addLayout(list_btns)

        self.new_btn.clicked.connect(self.new_requested.emit)
        self.dup_btn.clicked.connect(self.duplicate_requested.emit)
        self.delete_btn.clicked.connect(self.delete_requested.emit)
        return left

    def _build_right_panel(self) -> QWidget:
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)

        self.form_header = QLabel(_FORM_HEADER_IDLE)
        rl.addWidget(self.form_header)
        rl.addLayout(self._build_common_fields())
        rl.addWidget(_hline())

        self.stack = self._build_type_pages()
        rl.addWidget(self.stack, 1)
        self.f_type.currentIndexChanged.connect(self._on_type_changed)

        rl.addWidget(_hline())
        rl.addLayout(self._build_action_row())

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        rl.addWidget(self.status_label)
        rl.addStretch()

        for w in (self.f_name, self.f_description):
            w.textChanged.connect(self._mark_dirty)
        self.f_type.currentIndexChanged.connect(self._mark_dirty)

        return right

    def _build_common_fields(self) -> QFormLayout:
        """Build the name/type/description rows shared by every auth type."""
        top_form = QFormLayout()
        top_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.f_name = QLineEdit()
        self.f_type = QComboBox()
        for key, name in AUTH_TYPES.items():
            self.f_type.addItem(name, userData=key)
        self.f_description = QLineEdit()

        top_form.addRow("Name:*", self.f_name)
        top_form.addRow("Type:", self.f_type)
        top_form.addRow("Description:", self.f_description)
        return top_form

    def _build_type_pages(self) -> QStackedWidget:
        """Build the per-auth-type form pages, in ``_PAGE_ORDER``."""
        builders = {
            "oauth2": self._build_oauth2_page,
            "api_key": self._build_api_key_page,
            "basic": self._build_basic_page,
            "bearer": self._build_bearer_page,
            "aws_sigv4": self._build_aws_sigv4_page,
        }
        stack = QStackedWidget()
        for auth_type in _PAGE_ORDER:
            stack.addWidget(builders[auth_type]())
        return stack

    def _build_action_row(self) -> QHBoxLayout:
        """Build the Test / View Response / Set Default / Save button row."""
        act_row = QHBoxLayout()
        self.test_btn = QPushButton("🔌  Test Connection")
        self.view_response_btn = QPushButton("View Response…")
        self.default_btn = QPushButton("★  Set as Default")
        self.save_btn = QPushButton("💾  Save")

        for b in (self.test_btn, self.view_response_btn, self.default_btn, self.save_btn):
            b.setEnabled(False)
            act_row.addWidget(b)
        act_row.addStretch()

        self.test_btn.clicked.connect(self.test_requested.emit)
        self.view_response_btn.clicked.connect(self.view_response_requested.emit)
        self.default_btn.clicked.connect(self.set_default_requested.emit)
        self.save_btn.clicked.connect(self.save_requested.emit)
        return act_row

        # ── Type-specific form pages ──────────────────────────────────────

    def _build_oauth2_page(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.o_token_url = QLineEdit()
        self.o_token_url.setPlaceholderText("https://auth.example.com/oauth/token")
        self.o_client_id = QLineEdit()
        self.o_client_id.setPlaceholderText("client_id")
        self.o_client_secret = QLineEdit()
        self.o_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.o_client_secret.setPlaceholderText("client_secret")
        self.o_scope = QLineEdit()
        self.o_scope.setPlaceholderText("read write  (optional)")
        self.o_token_auth = QComboBox()
        self.o_token_auth.addItem("Body (RFC 6749 default)", userData="body")
        self.o_token_auth.addItem("HTTP Basic Auth (D&B Direct+, GitHub…)", userData="basic")
        self.o_token_auth.setToolTip(
            "How client credentials are sent to the token endpoint.\n"
            "• Body — client_id/client_secret in the POST body (default, RFC 6749 §2.3.1)\n"
            "• Basic — Base64-encoded Authorization header (required by some providers)",
        )
        self.o_grant_type = QComboBox()
        self.o_grant_type.addItems(list(GRANT_TYPES))
        self.o_extra = QTextEdit()
        self.o_extra.setPlaceholderText('{ "audience": "https://api.example.com" }')
        self.o_extra.setMaximumHeight(70)
        self.o_extra.setFont(get_mono_font())

        lay.addRow("Token URL:*", self.o_token_url)
        lay.addRow("Client ID:*", self.o_client_id)
        lay.addRow("Client Secret:", _secret_row(self.o_client_secret))
        lay.addRow("Scope:", self.o_scope)
        lay.addRow("Client Auth:", self.o_token_auth)
        lay.addRow("Grant Type:", self.o_grant_type)
        lay.addRow("Extra Params:", self.o_extra)

        for w2 in (self.o_token_url, self.o_client_id, self.o_client_secret, self.o_scope):
            w2.textChanged.connect(self._mark_dirty)
        self.o_token_auth.currentIndexChanged.connect(self._mark_dirty)
        self.o_grant_type.currentIndexChanged.connect(self._mark_dirty)
        self.o_extra.textChanged.connect(self._mark_dirty)
        return w

    def _build_api_key_page(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.ak_key = QLineEdit()
        self.ak_key.setPlaceholderText("X-API-Key")
        self.ak_value = QLineEdit()
        self.ak_value.setEchoMode(QLineEdit.EchoMode.Password)
        self.ak_value.setPlaceholderText("your-api-key")
        self.ak_location = QComboBox()
        self.ak_location.addItems(["header", "query"])

        lay.addRow("Header/Param Name:*", self.ak_key)
        lay.addRow("Key Value:*", _secret_row(self.ak_value))
        lay.addRow("Add To:", self.ak_location)

        for w2 in (self.ak_key, self.ak_value):
            w2.textChanged.connect(self._mark_dirty)
        self.ak_location.currentIndexChanged.connect(self._mark_dirty)
        return w

    def _build_basic_page(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.ba_username = QLineEdit()
        self.ba_username.setPlaceholderText("username")
        self.ba_password = QLineEdit()
        self.ba_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.ba_password.setPlaceholderText("password")

        lay.addRow("Username:*", self.ba_username)
        lay.addRow("Password:*", _secret_row(self.ba_password))

        for w2 in (self.ba_username, self.ba_password):
            w2.textChanged.connect(self._mark_dirty)
        return w

    def _build_bearer_page(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.bt_token = QLineEdit()
        self.bt_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.bt_token.setPlaceholderText("Bearer token")

        lay.addRow("Token:*", _secret_row(self.bt_token))

        self.bt_token.textChanged.connect(self._mark_dirty)
        return w

    def _build_aws_sigv4_page(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.aws_access_key = QLineEdit()
        self.aws_access_key.setPlaceholderText("AKIAIOSFODNN7EXAMPLE")
        self.aws_secret_key = QLineEdit()
        self.aws_secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.aws_secret_key.setPlaceholderText("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        self.aws_region = QLineEdit()
        self.aws_region.setPlaceholderText("us-east-1")
        self.aws_service = QLineEdit()
        self.aws_service.setPlaceholderText("execute-api")
        self.aws_session_token = QLineEdit()
        self.aws_session_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.aws_session_token.setPlaceholderText("Optional — STS session token")

        lay.addRow("Access Key ID:*", self.aws_access_key)
        lay.addRow("Secret Access Key:*", _secret_row(self.aws_secret_key))
        lay.addRow("Region:*", self.aws_region)
        lay.addRow("Service:*", self.aws_service)
        lay.addRow("Session Token:", _secret_row(self.aws_session_token))

        for w2 in (
            self.aws_access_key,
            self.aws_secret_key,
            self.aws_region,
            self.aws_service,
            self.aws_session_token,
        ):
            w2.textChanged.connect(self._mark_dirty)
        return w

    # --- public helpers used by controller ----------------------------

    def set_current_id(self, cred_id: int | None) -> None:
        self._current_id = cred_id
        self._set_form_enabled(cred_id is not None)
        self._sync_buttons()

    def get_form_data(self) -> tuple[str, str, str]:
        return (
            self.f_name.text().strip(),
            self.f_description.text().strip(),
            self.f_type.currentData(),
        )

    def mark_clean(self) -> None:
        """Clear the unsaved-changes flag and refresh the Save button label."""
        self._dirty = False
        self._sync_buttons()

    def clear_form(self) -> None:
        """Blank every field and return the form to its idle, disabled state."""
        for field in (
            self.f_name,
            self.f_description,
            self.o_token_url,
            self.o_client_id,
            self.o_client_secret,
            self.o_scope,
            self.ak_key,
            self.ak_value,
            self.ba_username,
            self.ba_password,
            self.bt_token,
            self.aws_access_key,
            self.aws_secret_key,
            self.aws_region,
            self.aws_service,
            self.aws_session_token,
        ):
            field.clear()
        self.o_extra.clear()
        self.set_current_id(None)
        self.mark_clean()

    def set_form_data(self, c: dict[str, Any]) -> None:
        cfg = c["config"] or {}
        auth_type = c["auth_type"]

        self.f_name.setText(c["name"])
        self.f_description.setText(c.get("description", ""))
        at_idx = self.f_type.findData(auth_type)
        self.f_type.setCurrentIndex(max(at_idx, 0))
        self._show_page_for(auth_type)
        self.form_header.setText(f"<b>Credential Details</b> — {c['name']}")

        if auth_type == "oauth2":
            self.o_token_url.setText(_text(cfg, "token_url"))
            self.o_client_id.setText(_text(cfg, "client_id"))
            self.o_client_secret.setText(_text(cfg, "client_secret"))
            self.o_scope.setText(_text(cfg, "scope"))
            ta_idx = self.o_token_auth.findData(cfg.get("token_auth") or "body")
            self.o_token_auth.setCurrentIndex(max(ta_idx, 0))
            gt_idx = self.o_grant_type.findText(cfg.get("grant_type") or "client_credentials")
            self.o_grant_type.setCurrentIndex(max(gt_idx, 0))
            extra = cfg.get("extra_params") or {}
            self.o_extra.setPlainText(json.dumps(extra, indent=2) if extra else "")
        elif auth_type == "api_key":
            self.ak_key.setText(_text(cfg, "key"))
            self.ak_value.setText(_text(cfg, "value"))
            loc_idx = self.ak_location.findText(cfg.get("location") or "header")
            self.ak_location.setCurrentIndex(max(loc_idx, 0))
        elif auth_type == "basic":
            self.ba_username.setText(_text(cfg, "username"))
            self.ba_password.setText(_text(cfg, "password"))
        elif auth_type == "bearer":
            self.bt_token.setText(_text(cfg, "token"))
        elif auth_type == "aws_sigv4":
            self.aws_access_key.setText(_text(cfg, "access_key"))
            self.aws_secret_key.setText(_text(cfg, "secret_key"))
            self.aws_region.setText(_text(cfg, "region", "us-east-1"))
            self.aws_service.setText(_text(cfg, "service", "execute-api"))
            self.aws_session_token.setText(_text(cfg, "session_token"))

        # Populating the widgets fires textChanged, so clear the flag last.
        self.mark_clean()

    def _show_page_for(self, auth_type: str) -> None:
        """Switch the stacked widget to the page for ``auth_type``."""
        try:
            self.stack.setCurrentIndex(_PAGE_ORDER.index(auth_type))
        except ValueError:
            logger.warning("No form page for auth type %r", auth_type)

    def show_validation_error(self, message: str) -> None:
        QMessageBox.warning(self, "Validation", message)

    def show_save_error(self, message: str) -> None:
        QMessageBox.critical(self, "Save Failed", message)

    def set_status(self, text: str, ok: bool | None = True) -> None:
        self.status_label.setText(self._format_status(text, ok))

    @staticmethod
    def _format_status(msg: str, ok: bool | None) -> str:
        """Return an HTML string for a coloured status message."""
        if ok is True:
            colour = Colors.GREEN
        elif ok is False:
            colour = Colors.RED
        else:
            colour = Colors.FG_MUTED
        return f"<span style='color:{colour};'>{msg}</span>"

    def update_list_items(self, items: list[tuple[int, str, dict[str, Any]]]) -> None:
        self.cred_list.clear()
        for cred_id, label, kwargs in items:
            item = QListWidgetItem(label)
            if "fg_color" in kwargs:
                item.setForeground(QColor(kwargs["fg_color"]))
            if "font" in kwargs:
                item.setFont(kwargs["font"])
            item.setData(Qt.ItemDataRole.UserRole, cred_id)
            self.cred_list.addItem(item)

    # --- internal view-only logic ------------------------------------

    def _on_item_selected(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        # Qt passes None when the list is cleared or the selection is dropped.
        if current is None:
            self.set_current_id(None)
            return
        cred_id = current.data(Qt.ItemDataRole.UserRole)
        self.set_current_id(int(cred_id))
        self.selection_changed.emit(int(cred_id))

    def _on_type_changed(self, index: int) -> None:
        auth_type = self.f_type.itemData(index)
        if auth_type:
            self._show_page_for(auth_type)
        self._update_test_btn()

    def _update_test_btn(self) -> None:
        auth_type = self.f_type.currentData()
        self.test_btn.setEnabled(self._current_id is not None and auth_type == "oauth2")

    def _set_form_enabled(self, enabled: bool) -> None:
        for w in (self.f_name, self.f_description, self.f_type, self.stack, self.default_btn, self.save_btn):
            w.setEnabled(enabled)
        if not enabled:
            self.test_btn.setEnabled(False)
            self.form_header.setText(_FORM_HEADER_IDLE)
            self.status_label.setText("")
        else:
            self._update_test_btn()

    def _sync_buttons(self) -> None:
        has = self._current_id is not None
        for b in (self.default_btn, self.save_btn):
            b.setEnabled(has)
        self.dup_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)
        if has:
            self._update_test_btn()
            self.view_response_btn.setEnabled(self._last_test_response is not None)
        else:
            self.test_btn.setEnabled(False)
            self.view_response_btn.setEnabled(False)
        self.save_btn.setText("\U0001f4be  Save *" if self._dirty else "\U0001f4be  Save")

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._sync_buttons()


class SavedCredentialsController(OAuthConnectionTestMixin):
    """Orchestrates interactions between view and service."""

    _test_btn_idle_text = "\U0001f50c  Test Connection"
    _test_btn_busy_text = "Testing…"

    def __init__(self, view: SavedCredentialsView, service: SavedCredentialsService) -> None:
        self._view = view
        self._service = service
        self._tester: OAuthTokenTester | None = None

        # The collector reads live widget values, so it can only be built once
        # a view exists.
        service.bind_collector(AuthConfigCollector(view))

        self._connect_signals()
        self._refresh_list()

    # ── Widgets the OAuth test mixin operates on live on the view ─────

    # The mixin declares these as plain attributes because dialog hosts assign
    # them directly; here they are owned by the view, so expose them read-only.
    @property
    def test_btn(self) -> QPushButton:  # type: ignore[override]
        return self._view.test_btn

    @property
    def view_response_btn(self) -> QPushButton:  # type: ignore[override]
        return self._view.view_response_btn

    @property
    def status_label(self) -> QLabel:
        return self._view.status_label

    @property
    def _last_test_response(self) -> dict[str, Any] | None:
        return self._view._last_test_response

    @_last_test_response.setter
    def _last_test_response(self, value: dict[str, Any] | None) -> None:
        # The view drives the View Response… button off this value.
        self._view._last_test_response = value

    def _format_status(self, msg: str, ok: bool | None) -> str:
        return self._view._format_status(msg, ok)

    def _connect_signals(self) -> None:
        v = self._view
        v.new_requested.connect(self._on_new)
        v.duplicate_requested.connect(self._on_duplicate)
        v.delete_requested.connect(self._on_delete)
        v.save_requested.connect(self._on_save)
        v.set_default_requested.connect(self._on_set_default)
        v.test_requested.connect(self._on_test)
        v.view_response_requested.connect(self._open_token_response)
        v.selection_changed.connect(self._on_selection_changed)

    def _refresh_list(self, select_id: int | None = None) -> None:
        items: list[tuple[int, str, dict[str, Any]]] = []
        for c in self._service.list_credentials():
            at = c["auth_type"]
            label = AUTH_TYPES.get(at, at)
            tag = " \u2605" if c["is_default"] else ""
            item_label = f"[{label}] {c['name']}{tag}"
            kwargs: dict[str, Any] = {"fg_color": _TYPE_COLOUR.get(at, Colors.FG)}
            if c["is_default"]:
                font = QFont()
                font.setBold(True)
                kwargs["font"] = font
            items.append((c["id"], item_label, kwargs))

        self._view.update_list_items(items)
        if select_id is not None:
            self._select_list_item(select_id)

    def _select_list_item(self, cred_id: int) -> None:
        """Select the list item with the given credential ID."""
        widget = self._view.cred_list
        for index in range(widget.count()):
            item = widget.item(index)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == cred_id:
                widget.setCurrentItem(item)
                return

    # --- handlers -----------------------------------------------------

    def _on_selection_changed(self, cred_id: int) -> None:
        c = self._service.get_credential(cred_id)
        if not c:
            return
        self._view.set_form_data(c)

    def _on_new(self) -> None:
        name, ok = QInputDialog.getText(self._view, "New Credential", "Credential name:")
        if not ok or not name.strip():
            return
        new_id = self._service.create_credential(name=name.strip(), auth_type="oauth2")
        self._refresh_list(select_id=new_id)
        self._view.credentials_changed.emit()

    def _on_duplicate(self) -> None:
        current_id = self._view._current_id
        if current_id is None:
            return
        src = self._service.get_credential(current_id)
        if not src:
            return
        suggested = self._service.manager.suggest_copy_name(src["name"])
        name, ok = QInputDialog.getText(
            self._view,
            "Duplicate Credential",
            "Name for the copy:",
            text=suggested,
        )
        if not ok or not name.strip():
            return
        new_id = self._service.duplicate_credential(current_id, new_name=name.strip())
        self._refresh_list(select_id=new_id)
        self._view.credentials_changed.emit()

    def _on_delete(self) -> None:
        current_id = self._view._current_id
        if current_id is None:
            return
        c = self._service.get_credential(current_id)
        if not c:
            return
        ans = QMessageBox.question(
            self._view,
            "Confirm Delete",
            f"Delete credential '{c['name']}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._service.delete_credential(current_id)
        self._view.clear_form()
        self._refresh_list()
        self._view.credentials_changed.emit()

    def _on_save(self) -> bool:
        """Persist the form. Returns ``True`` only when the write succeeded."""
        current_id = self._view._current_id
        if current_id is None:
            return False

        name, description, auth_type = self._view.get_form_data()
        if not name:
            self._view.show_validation_error("Name is required.")
            return False

        result = self._service.collect_config(auth_type)
        if result.config is None:
            self._view.show_validation_error(f"Configuration error: {result.error}")
            return False

        cfg = CredentialConfig(
            auth_type=auth_type,
            name=name,
            description=description,
            config=result.config,
        )
        try:
            self._service.update_credential(current_id, cfg)
        except Exception as exc:
            logger.error("Failed to save credential", exc_info=True)
            self._view.show_save_error(f"An error occurred: {exc}")
            return False

        self._refresh_list(select_id=current_id)
        self._view.mark_clean()
        self._view.set_status("\u2713 Saved", ok=True)
        self._view.credentials_changed.emit()
        return True

    def _on_set_default(self) -> None:
        current_id = self._view._current_id
        if current_id is None:
            return
        # Persist first; promoting an unsaved form would default to stale values.
        if not self._on_save():
            return
        self._service.set_default(current_id)
        self._refresh_list(select_id=current_id)
        self._view.set_status("\u2713 Set as default credential", ok=True)
        self._view.credentials_changed.emit()

    def _on_test(self) -> None:
        token_url = self._view.o_token_url.text().strip()
        client_id = self._view.o_client_id.text().strip()
        secret = self._view.o_client_secret.text()
        scope = self._view.o_scope.text().strip()
        token_auth = self._view.o_token_auth.currentData() or "body"
        grant_type = self._view.o_grant_type.currentText()
        extra_raw = self._view.o_extra.toPlainText().strip()

        self._start_oauth_test(
            token_url=token_url,
            client_id=client_id,
            secret=secret,
            scope=scope,
            grant_type=grant_type,
            extra_raw=extra_raw,
            token_auth=token_auth,
        )


class SavedCredentialsDialog(SavedCredentialsView):
    """Ready-to-use credential manager.

    Wires the view to its service and controller and keeps the controller
    alive for the dialog's lifetime, so callers only need a database handle.
    """

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = SavedCredentialsService(db=db)
        self._controller = SavedCredentialsController(view=self, service=self._service)

    # Convenience passthroughs so callers can drive the dialog directly.

    def _refresh_list(self, select_id: int | None = None) -> None:
        self._controller._refresh_list(select_id)

    def _new_cred(self) -> None:
        self._controller._on_new()
