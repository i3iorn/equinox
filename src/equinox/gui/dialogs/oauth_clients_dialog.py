"""OAuth2 client manager dialog.

Lets the user create, edit, test and delete named OAuth2 client credentials
that are stored independently of any collection or request.  A client marked
as *default* is pre-selected automatically in the Auth dialog.
"""

from __future__ import annotations

import json
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from equinox.gui.dialogs._list_form_dialog_mixin import ListFormDialogMixin
from equinox.gui.dialogs._oauth_connection_test_mixin import OAuthConnectionTestMixin
from equinox.gui.dialogs._oauth_form_utils import (
    parse_json_object_field,
)
from equinox.gui.theme import Colors, get_mono_font
from equinox.gui.widgets import make_secret_row
from equinox.gui.workers import OAuthTokenTester
from equinox.storage import Database, OAuthClientManager
from equinox.storage.oauth_clients import GRANT_TYPES

logger = logging.getLogger(__name__)

# HTML fragment shown in the form header when no client is loaded.
_FORM_HEADER_IDLE = (
    f"<b>Client Details</b>"
    f"<span style='color:{Colors.FG_MUTED};'>  (select a client to edit)</span>"
)


class OAuthClientsDialog(OAuthConnectionTestMixin, ListFormDialogMixin, QDialog):
    """Full-featured OAuth2 client credential manager.

    Layout
    ------
    Left  – scrollable list of saved clients (name + grant type + ✓ default)
    Right – edit form for the selected client, with:
              • all fields editable inline
              • show/hide eye toggle on secret
              • Test Connection button (fetches a real token)
              • Set as Default / Save / New / Delete buttons
    """

    # Emitted whenever the client list changes so callers can refresh pickers
    clients_changed = pyqtSignal()

    def __init__(self, db: Database, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.mgr = OAuthClientManager(db)
        self._current_id: int | None = None
        self._dirty = False
        self._tester: OAuthTokenTester | None = None  # kept alive until worker completion
        self._last_test_response: dict | None = None
        self._test_btn_idle_text = "🔌  Test Connection"
        self._test_btn_busy_text = "Testing…"

        # DirtyDialogMixin requirements
        self._save_callback = self._save_client

        self.setWindowTitle("OAuth2 Client Manager")
        self.setMinimumSize(860, 560)
        self._build_ui()
        # Set _list_widget after UI construction (required by ListFormDialogMixin)
        self._list_widget = self.client_list
        self._refresh_list()

    # ── UI construction ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([240, 620])
        root.addWidget(splitter, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self._on_close)
        root.addWidget(btns)

        self._set_form_enabled(False)

    def _build_left_panel(self) -> QWidget:
        """Scrollable client list with New / Delete buttons."""
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(QLabel("<b>OAuth2 Clients</b>"))

        self.client_list = QListWidget()
        self.client_list.setAlternatingRowColors(True)
        self.client_list.currentItemChanged.connect(self._on_item_selected)
        ll.addWidget(self.client_list, 1)

        list_btns = QHBoxLayout()
        self.new_btn = QPushButton("New…")
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setEnabled(False)
        list_btns.addWidget(self.new_btn)
        list_btns.addWidget(self.delete_btn)
        list_btns.addStretch()
        ll.addLayout(list_btns)

        self.new_btn.clicked.connect(self._new_client)
        self.delete_btn.clicked.connect(self._delete_client)
        return left

    def _build_right_panel(self) -> QWidget:
        """Build the right‑side OAuth client editor panel."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 0, 0)

        layout.addWidget(self._build_form_header())
        layout.addLayout(self._build_form_fields())
        layout.addWidget(self._build_separator())
        layout.addLayout(self._build_action_buttons())
        layout.addWidget(self._build_status_label())
        layout.addStretch()

        self._collect_form_widgets()
        self._wire_dirty_tracking()

        return panel

    def _build_form_header(self) -> QLabel:
        """Create the form header label."""
        self.form_header = QLabel(_FORM_HEADER_IDLE)
        return self.form_header

    def _build_form_fields(self) -> QFormLayout:
        """Create the OAuth client configuration form."""
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.f_name = QLineEdit()
        self.f_name.setPlaceholderText("My Service")

        self.f_description = QLineEdit()
        self.f_description.setPlaceholderText("Optional description")

        self.f_token_url = QLineEdit()
        self.f_token_url.setPlaceholderText("https://auth.example.com/oauth/token")

        self.f_client_id = QLineEdit()
        self.f_client_id.setPlaceholderText("client_id_here")

        self.f_client_secret = QLineEdit()
        self.f_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.f_client_secret.setPlaceholderText("client_secret_here")

        self.f_scope = QLineEdit()
        self.f_scope.setPlaceholderText("read write  (space-separated, optional)")

        self.f_grant_type = QComboBox()
        self.f_grant_type.addItems(list(GRANT_TYPES))

        self.f_token_auth = QComboBox()
        self.f_token_auth.addItem("Body (RFC 6749 default)", userData="body")
        self.f_token_auth.addItem("HTTP Basic Auth (D&B Direct+, GitHub…)", userData="basic")

        self.f_verify_ssl = QCheckBox("Verify token endpoint SSL certificates")
        self.f_verify_ssl.setChecked(True)

        self.f_extra = QTextEdit()
        self.f_extra.setPlaceholderText('{ "audience": "https://api.example.com" }')
        self.f_extra.setMaximumHeight(80)
        self.f_extra.setFont(get_mono_font())

        form.addRow("Name:*", self.f_name)
        form.addRow("Description:", self.f_description)
        form.addRow("Token URL:*", self.f_token_url)
        form.addRow("Client ID:*", self.f_client_id)
        form.addRow("Client Secret:", make_secret_row(self.f_client_secret))
        form.addRow("Scope:", self.f_scope)
        form.addRow("Grant Type:", self.f_grant_type)
        form.addRow("Client Auth:", self.f_token_auth)
        form.addRow("", self.f_verify_ssl)
        form.addRow("Extra Params:", self.f_extra)

        info = QLabel(
            f"<small style='color:{Colors.FG_MUTED};'>"
            f"Extra Params: JSON object merged into the token request body "
            f'(e.g. <tt>{{"audience": "…"}}</tt>).'
            f"</small>"
        )
        info.setWordWrap(True)
        form.addRow("", info)

        return form

    def _build_separator(self) -> QFrame:
        """Create a horizontal separator line."""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        return sep

    def _build_action_buttons(self) -> QHBoxLayout:
        """Create the Test / View / Default / Save button row."""
        row = QHBoxLayout()

        self.test_btn = QPushButton("🔌  Test Connection")
        self.view_response_btn = QPushButton("View Response…")
        self.view_response_btn.setEnabled(False)
        self.default_btn = QPushButton("★  Set as Default")
        self.save_btn = QPushButton("💾  Save")

        for btn in (self.test_btn, self.view_response_btn, self.default_btn, self.save_btn):
            btn.setEnabled(False)
            row.addWidget(btn)

        row.addStretch()

        self.test_btn.clicked.connect(self._test_client)
        self.view_response_btn.clicked.connect(self._view_test_response)
        self.default_btn.clicked.connect(self._set_default)
        self.save_btn.clicked.connect(self._save_client)

        return row

    def _build_status_label(self) -> QLabel:
        """Create the status message label."""
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        return self.status_label

    def _collect_form_widgets(self) -> None:
        """Collect editable widgets for enable/disable and dirty tracking."""
        self._line_fields = (
            self.f_name,
            self.f_description,
            self.f_token_url,
            self.f_client_id,
            self.f_client_secret,
            self.f_scope,
        )

        self._all_form_widgets = (
            *self._line_fields,
            self.f_grant_type,
            self.f_token_auth,
            self.f_verify_ssl,
            self.f_extra,
            self.test_btn,
            self.default_btn,
            self.save_btn,
        )

    def _wire_dirty_tracking(self) -> None:
        """Connect all form widgets to the dirty‑tracking handler."""
        for w in self._line_fields:
            w.textChanged.connect(self._mark_dirty)

        self.f_grant_type.currentIndexChanged.connect(self._mark_dirty)
        self.f_token_auth.currentIndexChanged.connect(self._mark_dirty)
        self.f_verify_ssl.stateChanged.connect(self._mark_dirty)
        self.f_extra.textChanged.connect(self._mark_dirty)

    # ── List management (ListFormDialogMixin template methods) ────────

    def _build_list_items(self):
        """Yield (item_id, label, kwargs) for each client."""
        from PyQt6.QtGui import QFont

        from equinox.gui.theme import Colors

        for c in self.mgr.list_clients():
            tag = " ★" if c["is_default"] else ""
            label = f"{c['name']}{tag}  [{c['grant_type']}]"
            kwargs = {}
            if c["is_default"]:
                kwargs["fg_color"] = Colors.GREEN
                font = QFont()
                font.setBold(True)
                kwargs["font"] = font
            yield c["id"], label, kwargs

    def _on_list_item_selected(self, client_id: int) -> None:
        """Load the client form."""
        self._load_form(client_id)

    # ── Selection logic ───────────────────────────────────────────────
    # _apply_selection() inherited from ListFormDialogMixin
    # _on_item_selected(current, _prev) inherited from ListFormDialogMixin

    def _load_form(self, client_id: int) -> None:
        c = self.mgr.get_client(client_id)
        if not c:
            return
        self._last_test_response = None
        self.view_response_btn.setEnabled(False)
        self._block_form(True)
        self.f_name.setText(c["name"])
        self.f_description.setText(c.get("description", ""))
        self.f_token_url.setText(c["token_url"])
        self.f_client_id.setText(c["client_id"])
        self.f_client_secret.setText(c.get("client_secret", ""))
        self.f_scope.setText(c.get("scope", ""))
        idx = self.f_grant_type.findText(c.get("grant_type", "client_credentials"))
        self.f_grant_type.setCurrentIndex(max(idx, 0))
        ta_idx = self.f_token_auth.findData(c.get("token_auth", "body") or "body")
        self.f_token_auth.setCurrentIndex(max(ta_idx, 0))
        self.f_verify_ssl.setChecked(bool(c.get("verify_ssl", True)))
        extra = c.get("extra_params", {})
        self.f_extra.setPlainText(json.dumps(extra, indent=2) if extra else "")
        self._block_form(False)
        self.form_header.setText(f"<b>Client: {c['name']}</b>")
        self.status_label.setText("")

    def _block_form(self, block: bool) -> None:
        for w in (
            *self._line_fields,
            self.f_extra,
            self.f_grant_type,
            self.f_token_auth,
            self.f_verify_ssl,
        ):
            w.blockSignals(block)

    def _set_form_enabled(self, enabled: bool) -> None:
        for w in self._all_form_widgets:
            w.setEnabled(enabled)
        if not enabled:
            self.form_header.setText(_FORM_HEADER_IDLE)
            self.status_label.setText("")

    def _sync_buttons(self) -> None:
        has = self._current_id is not None
        for b in (self.test_btn, self.default_btn, self.save_btn):
            b.setEnabled(has)
        self.delete_btn.setEnabled(has)
        self.view_response_btn.setEnabled(has and self._last_test_response is not None)
        self.save_btn.setText("💾  Save *" if self._dirty else "💾  Save")

    # ── CRUD ──────────────────────────────────────────────────────────

    def _new_client(self) -> None:
        name, ok = QInputDialog.getText(self, "New OAuth2 Client", "Client name:")
        if not ok or not name.strip():
            return
        try:
            new_id = self.mgr.create_client(
                name=name.strip(),
                token_url="",
                client_id="",
                client_secret="",
            )
            self._dirty = False
            self.clients_changed.emit()
            self._refresh_list(select_id=new_id)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _delete_client(self) -> None:
        if self._current_id is None:
            return
        c = self.mgr.get_client(self._current_id)
        if not c:
            return
        ans = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete OAuth2 client '{c['name']}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            self.mgr.delete_client(self._current_id)
            self._current_id = None
            self._dirty = False
            self.clients_changed.emit()
            self._refresh_list()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _save_client(self) -> bool:
        """Validate and persist the current form.  Returns True on success."""
        if self._current_id is None:
            return False

        name = self.f_name.text().strip()
        description = self.f_description.text().strip()
        token_url = self.f_token_url.text().strip()
        client_id = self.f_client_id.text().strip()
        secret = self.f_client_secret.text()
        scope = self.f_scope.text().strip()
        grant_type = self.f_grant_type.currentText()
        extra_raw = self.f_extra.toPlainText().strip()

        if not name:
            QMessageBox.warning(self, "Validation", "Name is required.")
            return False
        if not token_url:
            QMessageBox.warning(self, "Validation", "Token URL is required.")
            return False
        if not client_id:
            QMessageBox.warning(self, "Validation", "Client ID is required.")
            return False

        extra_params, error = parse_json_object_field(extra_raw)
        if extra_params is None:
            QMessageBox.warning(self, "Invalid Extra Params", error or "Invalid Extra Params")
            return False

        try:
            self.mgr.update_client(
                self._current_id,
                name=name,
                description=description,
                token_url=token_url,
                client_id_val=client_id,
                client_secret=secret,
                scope=scope,
                grant_type=grant_type,
                token_auth=str(self.f_token_auth.currentData() or "body"),
                verify_ssl=self.f_verify_ssl.isChecked(),
                extra_params=extra_params,
            )
            self._dirty = False
            self.clients_changed.emit()
            self._refresh_list(select_id=self._current_id)
            self._sync_buttons()
            self._set_status("✓ Saved", ok=True)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))
            return False

    def _set_default(self) -> None:
        if self._current_id is None:
            return
        if self._dirty and not self._save_client():
            return
        try:
            self.mgr.set_default(self._current_id)
            self.clients_changed.emit()
            self._refresh_list(select_id=self._current_id)
            self._set_status("✓ Set as default client", ok=True)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # ── Test connection ───────────────────────────────────────────────

    def _test_client(self) -> None:
        """Attempt to fetch a real token and display the outcome."""
        if self._current_id is None:
            return

        # Use the *form values* so the user can test before saving
        token_url = self.f_token_url.text().strip()
        client_id = self.f_client_id.text().strip()
        secret = self.f_client_secret.text()
        scope = self.f_scope.text().strip()
        grant_type = self.f_grant_type.currentText()
        token_auth = str(self.f_token_auth.currentData() or "body")
        extra_raw = self.f_extra.toPlainText().strip()

        self._start_oauth_test(
            token_url=token_url,
            client_id=client_id,
            secret=secret,
            scope=scope,
            grant_type=grant_type,
            extra_raw=extra_raw,
            token_auth=token_auth,
        )

    def _view_test_response(self) -> None:
        self._view_oauth_test_response()

    # ── Close guard ───────────────────────────────────────────────────
    # _on_close is inherited from DirtyDialogMixin
