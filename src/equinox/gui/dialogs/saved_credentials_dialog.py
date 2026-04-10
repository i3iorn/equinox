"""Saved credentials manager dialog.

Manages named, reusable auth credentials of any supported type
(OAuth 2.0, API Key, Basic Auth, Bearer Token).  Replaces the OAuth2-only
OAuthClientsDialog as the primary credential manager opened from AuthDialog.
"""

from __future__ import annotations

import json
import logging
from typing import Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter, QStackedWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

from equinox.gui.theme import Colors, get_mono_font
from equinox.gui.widgets.secret_row import make_secret_row as _secret_row
from equinox.gui.workers import OAuthTokenTester
from equinox.storage import Database
from equinox.storage.saved_credentials import (
    AUTH_TYPE_LABELS, AUTH_TYPES, SavedCredentialsManager,
)

logger = logging.getLogger(__name__)

# HTML fragment shown in the form header when no credential is loaded.
_FORM_HEADER_IDLE = (
    f"<b>Credential Details</b>"
    f"<span style='color:{Colors.FG_MUTED};'>  (select a credential)</span>"
)

GRANT_TYPES: Tuple[str, ...] = (
    "client_credentials", "refresh_token", "password", "authorization_code"
)

# Colour per auth type used in the list widget
_TYPE_COLOUR = {
    "oauth2":    Colors.BLUE,
    "api_key":   Colors.AMBER,
    "basic":     Colors.GREEN,
    "bearer":    Colors.PURPLE,
    "aws_sigv4": Colors.RED,
}


class SavedCredentialsDialog(QDialog):
    """Manager for named, reusable auth credentials of any type.

    Left panel  – scrollable list of all saved credentials grouped by type.
    Right panel – inline edit form whose fields switch based on the auth type,
                  plus Test (OAuth2 only), Set as Default, and Save buttons.
    """

    credentials_changed = pyqtSignal()

    def __init__(self, db: Database, parent=None) -> None:
        super().__init__(parent)
        self.db  = db
        self.mgr = SavedCredentialsManager(db)
        self._current_id: Optional[int] = None
        self._dirty = False
        self._tester: Optional[OAuthTokenTester] = None  # keeps reference alive until done

        self.setWindowTitle("Saved Credentials")
        self.setMinimumSize(960, 580)
        self._build_ui()
        self._refresh_list()

    # ── UI construction ───────────────────────────────────────────────

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
        close_btns.rejected.connect(self._on_close)
        root.addWidget(close_btns)

        self._set_form_enabled(False)

    def _build_left_panel(self) -> QWidget:
        """Scrollable credential list with New / Duplicate / Delete buttons."""
        left = QWidget()
        ll   = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(QLabel("<b>Saved Credentials</b>"))

        self.cred_list = QListWidget()
        self.cred_list.setAlternatingRowColors(True)
        self.cred_list.currentItemChanged.connect(self._on_item_selected)
        ll.addWidget(self.cred_list, 1)

        list_btns = QHBoxLayout()
        self.new_btn    = QPushButton("New\u2026")
        self.dup_btn    = QPushButton("Duplicate")
        self.delete_btn = QPushButton("Delete")
        self.dup_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        for b in (self.new_btn, self.dup_btn, self.delete_btn):
            list_btns.addWidget(b)
        list_btns.addStretch()
        ll.addLayout(list_btns)

        self.new_btn.clicked.connect(self._new_cred)
        self.dup_btn.clicked.connect(self._duplicate_cred)
        self.delete_btn.clicked.connect(self._delete_cred)
        return left

    def _build_right_panel(self) -> QWidget:
        """Inline edit form with type-specific stacked pages and action buttons."""
        right = QWidget()
        rl    = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)

        self.form_header = QLabel(_FORM_HEADER_IDLE)
        rl.addWidget(self.form_header)

        # Name / type / description — common to all auth types
        top_form = QFormLayout()
        top_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.f_name = QLineEdit()
        self.f_name.setPlaceholderText("My Credential")
        self.f_type = QComboBox()
        for at in AUTH_TYPES:
            self.f_type.addItem(AUTH_TYPE_LABELS[at], userData=at)
        self.f_description = QLineEdit()
        self.f_description.setPlaceholderText("Optional description")
        top_form.addRow("Name:*",       self.f_name)
        top_form.addRow("Type:",        self.f_type)
        top_form.addRow("Description:", self.f_description)
        rl.addLayout(top_form)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        rl.addWidget(sep1)

        # Type-specific stacked pages (order matches AUTH_TYPES tuple)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_oauth2_page())    # 0
        self.stack.addWidget(self._build_api_key_page())   # 1
        self.stack.addWidget(self._build_basic_page())     # 2
        self.stack.addWidget(self._build_bearer_page())    # 3
        self.stack.addWidget(self._build_aws_sigv4_page()) # 4
        rl.addWidget(self.stack, 1)

        self.f_type.currentIndexChanged.connect(self._on_type_changed)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        rl.addWidget(sep2)

        # Action buttons
        act_row = QHBoxLayout()
        self.test_btn    = QPushButton("\U0001f50c  Test Connection")
        self.default_btn = QPushButton("\u2605  Set as Default")
        self.save_btn    = QPushButton("\U0001f4be  Save")
        self.save_btn.setStyleSheet("font-weight: bold;")
        for b in (self.test_btn, self.default_btn, self.save_btn):
            b.setEnabled(False)
            act_row.addWidget(b)
        act_row.addStretch()
        rl.addLayout(act_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        rl.addWidget(self.status_label)

        self.test_btn.clicked.connect(self._test_cred)
        self.default_btn.clicked.connect(self._set_default)
        self.save_btn.clicked.connect(self._save_cred)

        rl.addStretch()

        # Dirty tracking for common fields
        for w in (self.f_name, self.f_description):
            w.textChanged.connect(self._mark_dirty)
        self.f_type.currentIndexChanged.connect(self._mark_dirty)

        return right

    # ── Type-specific form pages ──────────────────────────────────────

    def _build_oauth2_page(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.o_token_url     = QLineEdit()
        self.o_token_url.setPlaceholderText("https://auth.example.com/oauth/token")
        self.o_client_id     = QLineEdit()
        self.o_client_id.setPlaceholderText("client_id")
        self.o_client_secret = QLineEdit()
        self.o_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.o_client_secret.setPlaceholderText("client_secret")
        self.o_scope         = QLineEdit()
        self.o_scope.setPlaceholderText("read write  (optional)")
        self.o_grant_type    = QComboBox()
        self.o_grant_type.addItems(list(GRANT_TYPES))
        self.o_extra         = QTextEdit()
        self.o_extra.setPlaceholderText('{ "audience": "https://api.example.com" }')
        self.o_extra.setMaximumHeight(70)
        self.o_extra.setFont(get_mono_font())

        lay.addRow("Token URL:*",    self.o_token_url)
        lay.addRow("Client ID:*",    self.o_client_id)
        lay.addRow("Client Secret:", _secret_row(self.o_client_secret))
        lay.addRow("Scope:",         self.o_scope)
        lay.addRow("Grant Type:",    self.o_grant_type)
        lay.addRow("Extra Params:",  self.o_extra)

        for w2 in (self.o_token_url, self.o_client_id, self.o_client_secret, self.o_scope):
            w2.textChanged.connect(self._mark_dirty)
        self.o_grant_type.currentIndexChanged.connect(self._mark_dirty)
        self.o_extra.textChanged.connect(self._mark_dirty)
        return w

    def _build_api_key_page(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.ak_key      = QLineEdit()
        self.ak_key.setPlaceholderText("X-API-Key")
        self.ak_value    = QLineEdit()
        self.ak_value.setEchoMode(QLineEdit.EchoMode.Password)
        self.ak_value.setPlaceholderText("your-api-key")
        self.ak_location = QComboBox()
        self.ak_location.addItems(["header", "query"])

        lay.addRow("Header/Param Name:*", self.ak_key)
        lay.addRow("Key Value:*",         _secret_row(self.ak_value))
        lay.addRow("Add To:",             self.ak_location)

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

        self.aws_access_key    = QLineEdit()
        self.aws_access_key.setPlaceholderText("AKIAIOSFODNN7EXAMPLE")
        self.aws_secret_key    = QLineEdit()
        self.aws_secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.aws_secret_key.setPlaceholderText("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        self.aws_region        = QLineEdit()
        self.aws_region.setPlaceholderText("us-east-1")
        self.aws_service       = QLineEdit()
        self.aws_service.setPlaceholderText("execute-api")
        self.aws_session_token = QLineEdit()
        self.aws_session_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.aws_session_token.setPlaceholderText("Optional — STS session token")

        lay.addRow("Access Key ID:*",    self.aws_access_key)
        lay.addRow("Secret Access Key:*", _secret_row(self.aws_secret_key))
        lay.addRow("Region:*",            self.aws_region)
        lay.addRow("Service:*",           self.aws_service)
        lay.addRow("Session Token:",      _secret_row(self.aws_session_token))

        for w2 in (self.aws_access_key, self.aws_secret_key,
                   self.aws_region, self.aws_service, self.aws_session_token):
            w2.textChanged.connect(self._mark_dirty)
        return w

    # ── Type combo callback ───────────────────────────────────────────

    def _on_type_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self._update_test_btn()

    def _update_test_btn(self) -> None:
        auth_type = self.f_type.currentData()
        self.test_btn.setEnabled(
            self._current_id is not None and auth_type == "oauth2"
        )

    # ── List management ───────────────────────────────────────────────

    def _refresh_list(self, select_id: Optional[int] = None) -> None:
        self.cred_list.setUpdatesEnabled(False)
        self.cred_list.blockSignals(True)
        try:
            self.cred_list.clear()
            for c in self.mgr.list():
                at    = c["auth_type"]
                label = AUTH_TYPE_LABELS.get(at, at)
                tag   = " \u2605" if c["is_default"] else ""
                item  = QListWidgetItem(f"[{label}] {c['name']}{tag}")
                item.setData(Qt.ItemDataRole.UserRole, c["id"])
                item.setForeground(QColor(_TYPE_COLOUR.get(at, Colors.FG)))
                if c["is_default"]:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.cred_list.addItem(item)
                if c["id"] == select_id:
                    self.cred_list.setCurrentItem(item)
        finally:
            self.cred_list.blockSignals(False)
            self.cred_list.setUpdatesEnabled(True)

        if select_id is None and self.cred_list.count():
            # Fires currentItemChanged → _on_item_selected
            self.cred_list.setCurrentRow(0)
        else:
            # Item was selected while signals were blocked — drive
            # selection logic manually so the form gets loaded/enabled.
            self._apply_selection()

    def _apply_selection(self) -> None:
        """Read the currently-selected list item and load it into the form.

        Does NOT prompt about unsaved changes — only called after
        programmatic list rebuilds where dirty state has been resolved.
        """
        current = self.cred_list.currentItem()
        if current is None:
            self._current_id = None
            self._set_form_enabled(False)
            self.dup_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            return

        self._current_id = current.data(Qt.ItemDataRole.UserRole)
        self._load_form(self._current_id)
        self._set_form_enabled(True)
        self._dirty = False
        self._sync_buttons()

    def _on_item_selected(self, current: QListWidgetItem, _prev) -> None:
        if current is None:
            self._current_id = None
            self._set_form_enabled(False)
            return

        new_id = current.data(Qt.ItemDataRole.UserRole)
        if new_id == self._current_id:
            return  # same item re-selected — no-op

        if self._dirty and self._current_id is not None:
            ans = QMessageBox.question(
                self, "Unsaved Changes",
                "Save changes to the current credential before switching?",
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                self._reselect(self._current_id)
                return
            if ans == QMessageBox.StandardButton.Save:
                if not self._save_cred():
                    self._reselect(self._current_id)
                    return

        self._current_id = new_id
        self._load_form(self._current_id)
        self._set_form_enabled(True)
        self._dirty = False
        self._sync_buttons()

    def _reselect(self, cred_id: int) -> None:
        self.cred_list.blockSignals(True)
        for i in range(self.cred_list.count()):
            if self.cred_list.item(i).data(Qt.ItemDataRole.UserRole) == cred_id:
                self.cred_list.setCurrentRow(i)
                break
        self.cred_list.blockSignals(False)

    def _load_form(self, cred_id: int) -> None:
        c = self.mgr.get(cred_id)
        if not c:
            return
        cfg = c["config"]

        self._block_form(True)
        self.f_name.setText(c["name"])
        self.f_description.setText(c.get("description", ""))

        at_idx = self.f_type.findData(c["auth_type"])
        idx    = max(at_idx, 0)
        self.f_type.setCurrentIndex(idx)
        self.stack.setCurrentIndex(idx)

        if c["auth_type"] == "oauth2":
            self.o_token_url.setText(cfg.get("token_url", ""))
            self.o_client_id.setText(cfg.get("client_id", ""))
            self.o_client_secret.setText(cfg.get("client_secret", ""))
            self.o_scope.setText(cfg.get("scope", ""))
            gt_idx = self.o_grant_type.findText(
                cfg.get("grant_type", "client_credentials")
            )
            self.o_grant_type.setCurrentIndex(max(gt_idx, 0))
            extra = cfg.get("extra_params", {})
            self.o_extra.setPlainText(json.dumps(extra, indent=2) if extra else "")
        elif c["auth_type"] == "api_key":
            self.ak_key.setText(cfg.get("key", ""))
            self.ak_value.setText(cfg.get("value", ""))
            loc_idx = self.ak_location.findText(cfg.get("location", "header"))
            self.ak_location.setCurrentIndex(max(loc_idx, 0))
        elif c["auth_type"] == "basic":
            self.ba_username.setText(cfg.get("username", ""))
            self.ba_password.setText(cfg.get("password", ""))
        elif c["auth_type"] == "bearer":
            self.bt_token.setText(cfg.get("token", ""))
        elif c["auth_type"] == "aws_sigv4":
            self.aws_access_key.setText(cfg.get("access_key", ""))
            self.aws_secret_key.setText(cfg.get("secret_key", ""))
            self.aws_region.setText(cfg.get("region", "us-east-1"))
            self.aws_service.setText(cfg.get("service", "execute-api"))
            self.aws_session_token.setText(cfg.get("session_token", ""))

        self._block_form(False)
        self.form_header.setText(
            f"<b>{c['name']}</b>"
            f"  <small style='color:{Colors.FG_MUTED};'>"
            f"[{AUTH_TYPE_LABELS.get(c['auth_type'], c['auth_type'])}]"
            f"</small>"
        )
        self.status_label.setText("")

    def _block_form(self, block: bool) -> None:
        for w in (
            self.f_name, self.f_description,
            self.o_token_url, self.o_client_id, self.o_client_secret,
            self.o_scope, self.o_extra,
            self.ak_key, self.ak_value,
            self.ba_username, self.ba_password,
            self.bt_token,
            self.aws_access_key, self.aws_secret_key,
            self.aws_region, self.aws_service, self.aws_session_token,
        ):
            w.blockSignals(block)
        for cb in (self.f_type, self.o_grant_type, self.ak_location):
            cb.blockSignals(block)

    def _set_form_enabled(self, enabled: bool) -> None:
        for w in (
            self.f_name, self.f_description, self.f_type, self.stack,
            self.default_btn, self.save_btn,
        ):
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
        else:
            self.test_btn.setEnabled(False)
        self.save_btn.setStyleSheet(
            "font-weight: bold; color: #9a6700;" if self._dirty
            else "font-weight: bold;"
        )
        self.save_btn.setText(
            "\U0001f4be  Save *" if self._dirty else "\U0001f4be  Save"
        )

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            self._sync_buttons()

    # ── CRUD ──────────────────────────────────────────────────────────

    def _new_cred(self) -> None:
        name, ok = QInputDialog.getText(self, "New Credential", "Credential name:")
        if not ok or not name.strip():
            return
        try:
            new_id = self.mgr.create(name=name.strip(), auth_type="oauth2")
            self._dirty = False
            self.credentials_changed.emit()
            self._refresh_list(select_id=new_id)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _duplicate_cred(self) -> None:
        if self._current_id is None:
            return
        src = self.mgr.get(self._current_id)
        if not src:
            return
        suggested = self.mgr._unique_copy_name(src["name"])
        name, ok = QInputDialog.getText(
            self, "Duplicate Credential",
            "Name for the copy:", text=suggested,
        )
        if not ok or not name.strip():
            return
        try:
            new_id = self.mgr.duplicate(self._current_id, new_name=name.strip())
            self._dirty = False
            self.credentials_changed.emit()
            self._refresh_list(select_id=new_id)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _delete_cred(self) -> None:
        if self._current_id is None:
            return
        c = self.mgr.get(self._current_id)
        if not c:
            return
        ans = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete credential '{c['name']}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            self.mgr.delete(self._current_id)
            self._current_id = None
            self._dirty = False
            self.credentials_changed.emit()
            self._refresh_list()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _save_cred(self) -> bool:
        """Validate and persist the current form.  Returns True on success."""
        if self._current_id is None:
            return False

        name        = self.f_name.text().strip()
        description = self.f_description.text().strip()
        auth_type   = self.f_type.currentData()

        if not name:
            QMessageBox.warning(self, "Validation", "Name is required.")
            return False

        config, error = self._collect_config(auth_type)
        if config is None:
            QMessageBox.warning(self, "Validation", error)
            return False

        try:
            self.mgr.update(
                self._current_id,
                name=name,
                auth_type=auth_type,
                config=config,
                description=description,
            )
            self._dirty = False
            self.credentials_changed.emit()
            self._refresh_list(select_id=self._current_id)
            self._sync_buttons()
            self._set_status("\u2713 Saved", ok=True)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))
            return False

    def _collect_config(self, auth_type: str) -> Tuple[Optional[dict], Optional[str]]:
        """Read type-specific form fields.  Returns (config_dict, None) or (None, error)."""
        if auth_type == "oauth2":
            token_url = self.o_token_url.text().strip()
            client_id = self.o_client_id.text().strip()
            if not token_url:
                return None, "Token URL is required for OAuth 2.0."
            if not client_id:
                return None, "Client ID is required for OAuth 2.0."
            extra_raw = self.o_extra.toPlainText().strip()
            extra_params: dict = {}
            if extra_raw:
                try:
                    extra_params = json.loads(extra_raw)
                    if not isinstance(extra_params, dict):
                        raise ValueError("must be a JSON object")
                except (json.JSONDecodeError, ValueError) as exc:
                    return None, f"Extra Params must be a valid JSON object:\n{exc}"
            return {
                "token_url":     token_url,
                "client_id":     client_id,
                "client_secret": self.o_client_secret.text(),
                "scope":         self.o_scope.text().strip(),
                "grant_type":    self.o_grant_type.currentText(),
                "extra_params":  extra_params,
            }, None

        if auth_type == "api_key":
            key   = self.ak_key.text().strip()
            value = self.ak_value.text()
            if not key:
                return None, "Header/Param Name is required for API Key."
            if not value:
                return None, "Key Value is required for API Key."
            return {"key": key, "value": value, "location": self.ak_location.currentText()}, None

        if auth_type == "basic":
            username = self.ba_username.text().strip()
            password = self.ba_password.text()
            if not username:
                return None, "Username is required for Basic Auth."
            if not password:
                return None, "Password is required for Basic Auth."
            return {"username": username, "password": password}, None

        if auth_type == "bearer":
            token = self.bt_token.text().strip()
            if not token:
                return None, "Token is required for Bearer Token."
            return {"token": token}, None

        if auth_type == "aws_sigv4":
            access_key = self.aws_access_key.text().strip()
            secret_key = self.aws_secret_key.text().strip()
            region     = self.aws_region.text().strip()
            service    = self.aws_service.text().strip()
            if not access_key:
                return None, "Access Key ID is required for AWS SigV4."
            if not secret_key:
                return None, "Secret Access Key is required for AWS SigV4."
            if not region:
                return None, "Region is required for AWS SigV4."
            if not service:
                return None, "Service is required for AWS SigV4."
            config: dict = {
                "access_key": access_key,
                "secret_key": secret_key,
                "region":     region,
                "service":    service,
            }
            session_token = self.aws_session_token.text().strip()
            if session_token:
                config["session_token"] = session_token
            return config, None

        return {}, None

    def _set_default(self) -> None:
        if self._current_id is None:
            return
        if self._dirty and not self._save_cred():
            return
        try:
            self.mgr.set_default(self._current_id)
            self.credentials_changed.emit()
            self._refresh_list(select_id=self._current_id)
            self._set_status("\u2713 Set as default credential", ok=True)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # ── Test connection (OAuth2 only) ─────────────────────────────────

    def _test_cred(self) -> None:
        token_url  = self.o_token_url.text().strip()
        client_id  = self.o_client_id.text().strip()
        secret     = self.o_client_secret.text()
        scope      = self.o_scope.text().strip()
        grant_type = self.o_grant_type.currentText()
        extra_raw  = self.o_extra.toPlainText().strip()

        if not token_url or not client_id:
            QMessageBox.warning(
                self, "Missing Fields",
                "Token URL and Client ID are required to test the connection."
            )
            return

        extra_params: dict = {}
        if extra_raw:
            try:
                extra_params = json.loads(extra_raw)
            except json.JSONDecodeError:
                pass

        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing\u2026")
        self._set_status("Connecting\u2026", ok=None)

        self._tester = OAuthTokenTester(
            token_url, client_id, secret, scope, grant_type, extra_params
        )
        self._tester.done.connect(self._on_test_done)
        self._tester.start()

    def _on_test_done(self, success: bool, message: str) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText("\U0001f50c  Test Connection")
        self._set_status(message, ok=success)

    # ── Close guard ───────────────────────────────────────────────────

    def _on_close(self) -> None:
        if self._dirty:
            ans = QMessageBox.question(
                self, "Unsaved Changes",
                "Save changes before closing?",
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
            if ans == QMessageBox.StandardButton.Save:
                if not self._save_cred():
                    return  # save failed — keep dialog open
        self.accept()

    def _set_status(self, msg: str, ok: Optional[bool]) -> None:
        colour = Colors.GREEN if ok is True else Colors.RED if ok is False else Colors.FG_MUTED
        self.status_label.setText(f"<span style='color:{colour};'>{msg}</span>")
