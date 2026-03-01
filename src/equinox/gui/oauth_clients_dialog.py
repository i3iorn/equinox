"""OAuth2 client manager dialog.

Lets the user create, edit, test and delete named OAuth2 client credentials
that are stored independently of any collection or request.  A client marked
as *default* is pre-selected automatically in the Auth dialog.
"""

import json
import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QWidget, QFormLayout, QLineEdit, QComboBox, QTextEdit,
    QDialogButtonBox, QMessageBox, QInputDialog, QToolButton,
    QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from equinox.gui.theme import Colors, get_mono_font
from equinox.storage import Database, OAuthClientManager
from equinox.storage.oauth_clients import GRANT_TYPES

from typing import Optional

logger = logging.getLogger(__name__)


# ── Show/hide helper (reused from auth_dialog) ────────────────────────────────

def _secret_row(field: QLineEdit) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(2)
    row.addWidget(field, 1)
    btn = QToolButton()
    btn.setCheckable(True)
    btn.setText("👁")
    btn.setFixedWidth(28)
    btn.setToolTip("Show / hide")
    btn.setStyleSheet("QToolButton { border: none; font-size: 14px; }")
    btn.toggled.connect(
        lambda checked: field.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
    )
    row.addWidget(btn)
    return row


class OAuthClientsDialog(QDialog):
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

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db  = db
        self.mgr = OAuthClientManager(db)
        self._current_id: Optional[int] = None
        self._dirty = False

        self.setWindowTitle("OAuth2 Client Manager")
        self.setMinimumSize(860, 560)
        self._build_ui()
        self._refresh_list()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: client list ─────────────────────────────────────────
        left = QWidget()
        ll   = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(QLabel("<b>OAuth2 Clients</b>"))

        self.client_list = QListWidget()
        self.client_list.setAlternatingRowColors(True)
        self.client_list.currentItemChanged.connect(self._on_client_selected)
        ll.addWidget(self.client_list, 1)

        list_btns = QHBoxLayout()
        self.new_btn    = QPushButton("New…")
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setEnabled(False)
        list_btns.addWidget(self.new_btn)
        list_btns.addWidget(self.delete_btn)
        list_btns.addStretch()
        ll.addLayout(list_btns)
        self.new_btn.clicked.connect(self._new_client)
        self.delete_btn.clicked.connect(self._delete_client)

        splitter.addWidget(left)

        # ── Right: edit form ──────────────────────────────────────────
        right = QWidget()
        rl    = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)

        self.form_header = QLabel(
            f"<b>Client Details</b>"
            f"<span style='color:{Colors.FG_MUTED};'>"
            f"  (select a client to edit)</span>"
        )
        rl.addWidget(self.form_header)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.f_name        = QLineEdit(); self.f_name.setPlaceholderText("My Service")
        self.f_description = QLineEdit(); self.f_description.setPlaceholderText("Optional description")
        self.f_token_url   = QLineEdit(); self.f_token_url.setPlaceholderText("https://auth.example.com/oauth/token")
        self.f_client_id   = QLineEdit(); self.f_client_id.setPlaceholderText("client_id_here")
        self.f_client_secret = QLineEdit()
        self.f_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.f_client_secret.setPlaceholderText("client_secret_here")
        self.f_scope       = QLineEdit(); self.f_scope.setPlaceholderText("read write  (space-separated, optional)")
        self.f_grant_type  = QComboBox()
        self.f_grant_type.addItems(list(GRANT_TYPES))
        self.f_extra       = QTextEdit()
        self.f_extra.setPlaceholderText('{ "audience": "https://api.example.com" }')
        self.f_extra.setMaximumHeight(80)
        self.f_extra.setFont(get_mono_font())

        form.addRow("Name:*",         self.f_name)
        form.addRow("Description:",   self.f_description)
        form.addRow("Token URL:*",    self.f_token_url)
        form.addRow("Client ID:*",    self.f_client_id)
        form.addRow("Client Secret:", _secret_row(self.f_client_secret))
        form.addRow("Scope:",         self.f_scope)
        form.addRow("Grant Type:",    self.f_grant_type)
        form.addRow("Extra Params:",  self.f_extra)

        # Info label below extra params
        info = QLabel(
            f"<small style='color:{Colors.FG_MUTED};'>"
            f"Extra Params: JSON object merged into the token request body "
            f"(e.g. <tt>{{\"audience\": \"…\"}}</tt>)."
            f"</small>"
        )
        info.setWordWrap(True)
        form.addRow("", info)

        rl.addLayout(form)

        # Mark dirty on any change
        for w in (self.f_name, self.f_description, self.f_token_url,
                  self.f_client_id, self.f_client_secret, self.f_scope):
            w.textChanged.connect(self._mark_dirty)
        self.f_grant_type.currentIndexChanged.connect(self._mark_dirty)
        self.f_extra.textChanged.connect(self._mark_dirty)

        # ── Action buttons ────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        rl.addWidget(sep)

        act_row = QHBoxLayout()
        self.test_btn    = QPushButton("🔌  Test Connection")
        self.default_btn = QPushButton("★  Set as Default")
        self.save_btn    = QPushButton("💾  Save")
        self.save_btn.setStyleSheet("font-weight: bold;")

        for b in (self.test_btn, self.default_btn, self.save_btn):
            b.setEnabled(False)
            act_row.addWidget(b)
        act_row.addStretch()
        rl.addLayout(act_row)

        self.test_btn.clicked.connect(self._test_client)
        self.default_btn.clicked.connect(self._set_default)
        self.save_btn.clicked.connect(self._save_client)

        # Status line for test results
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        rl.addWidget(self.status_label)

        rl.addStretch()

        splitter.addWidget(right)
        splitter.setSizes([240, 620])
        root.addWidget(splitter, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self._on_close)
        root.addWidget(btns)

        self._set_form_enabled(False)

    # ── List management ───────────────────────────────────────────────

    def _refresh_list(self, select_id: Optional[int] = None):
        self.client_list.blockSignals(True)
        self.client_list.clear()
        clients = self.mgr.list_clients()
        for c in clients:
            tag   = " ★" if c["is_default"] else ""
            label = f"{c['name']}{tag}  [{c['grant_type']}]"
            item  = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            if c["is_default"]:
                item.setForeground(QColor(Colors.GREEN))
                f = item.font(); f.setBold(True); item.setFont(f)
            self.client_list.addItem(item)
            if c["id"] == select_id:
                self.client_list.setCurrentItem(item)
        self.client_list.blockSignals(False)

        if select_id is None and self.client_list.count():
            # Triggers currentItemChanged → _on_client_selected
            self.client_list.setCurrentRow(0)
        else:
            # Item was selected while signals were blocked, so the
            # _on_client_selected slot never ran.  Drive the selection
            # logic manually using what is actually selected in the list.
            self._apply_selection()

    # ── Selection logic ───────────────────────────────────────────────

    def _apply_selection(self):
        """Read the currently-selected list item and load it into the form.

        Unlike ``_on_client_selected`` (the signal slot) this method does
        NOT prompt about unsaved changes — it is only called after a
        programmatic list rebuild where the dirty state has already been
        resolved by the caller (create / delete / save / set-default).
        """
        current = self.client_list.currentItem()
        if current is None:
            self._current_id = None
            self._set_form_enabled(False)
            self.delete_btn.setEnabled(False)
            return

        self._current_id = current.data(Qt.ItemDataRole.UserRole)
        self._load_form(self._current_id)
        self._set_form_enabled(True)
        self.delete_btn.setEnabled(True)
        self._dirty = False
        self._sync_buttons()

    def _on_client_selected(self, current: QListWidgetItem, _prev):
        """Handle interactive selection changes from the list widget."""
        if current is None:
            self._current_id = None
            self._set_form_enabled(False)
            self.delete_btn.setEnabled(False)
            return

        new_id = current.data(Qt.ItemDataRole.UserRole)
        if new_id == self._current_id:
            return   # same item re-selected — no-op

        if self._dirty and self._current_id is not None:
            ans = QMessageBox.question(
                self, "Unsaved Changes",
                "Save changes to the current client before switching?",
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                self._reselect(self._current_id)
                return
            if ans == QMessageBox.StandardButton.Save:
                if not self._save_client():
                    self._reselect(self._current_id)
                    return

        self._current_id = new_id
        self._load_form(self._current_id)
        self._set_form_enabled(True)
        self.delete_btn.setEnabled(True)
        self._dirty = False
        self._sync_buttons()

    def _reselect(self, client_id: int):
        self.client_list.blockSignals(True)
        for i in range(self.client_list.count()):
            if self.client_list.item(i).data(Qt.ItemDataRole.UserRole) == client_id:
                self.client_list.setCurrentRow(i)
                break
        self.client_list.blockSignals(False)

    def _load_form(self, client_id: int):
        c = self.mgr.get_client(client_id)
        if not c:
            return
        self._block_form(True)
        self.f_name.setText(c["name"])
        self.f_description.setText(c.get("description", ""))
        self.f_token_url.setText(c["token_url"])
        self.f_client_id.setText(c["client_id"])
        self.f_client_secret.setText(c.get("client_secret", ""))
        self.f_scope.setText(c.get("scope", ""))
        idx = self.f_grant_type.findText(c.get("grant_type", "client_credentials"))
        self.f_grant_type.setCurrentIndex(max(idx, 0))
        extra = c.get("extra_params", {})
        self.f_extra.setPlainText(
            json.dumps(extra, indent=2) if extra else ""
        )
        self._block_form(False)
        self.form_header.setText(f"<b>Client: {c['name']}</b>")
        self.status_label.setText("")

    def _block_form(self, block: bool):
        for w in (self.f_name, self.f_description, self.f_token_url,
                  self.f_client_id, self.f_client_secret, self.f_scope,
                  self.f_extra):
            w.blockSignals(block)
        self.f_grant_type.blockSignals(block)

    def _set_form_enabled(self, enabled: bool):
        for w in (self.f_name, self.f_description, self.f_token_url,
                  self.f_client_id, self.f_client_secret, self.f_scope,
                  self.f_grant_type, self.f_extra,
                  self.test_btn, self.default_btn, self.save_btn):
            w.setEnabled(enabled)
        if not enabled:
            self.form_header.setText(
                f"<b>Client Details</b>"
                f"<span style='color:{Colors.FG_MUTED};'>"
                f"  (select a client to edit)</span>"
            )
            self.status_label.setText("")

    def _sync_buttons(self):
        has = self._current_id is not None
        for b in (self.test_btn, self.default_btn, self.save_btn):
            b.setEnabled(has)
        self.delete_btn.setEnabled(has)
        self.save_btn.setStyleSheet(
            "font-weight: bold; color: #9a6700;" if self._dirty
            else "font-weight: bold;"
        )
        self.save_btn.setText(
            "💾  Save *" if self._dirty else "💾  Save"
        )

    def _mark_dirty(self):
        self._dirty = True
        self._sync_buttons()

    # ── CRUD ──────────────────────────────────────────────────────────

    def _new_client(self):
        name, ok = QInputDialog.getText(
            self, "New OAuth2 Client", "Client name:"
        )
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

    def _delete_client(self):
        if self._current_id is None:
            return
        c = self.mgr.get_client(self._current_id)
        if not c:
            return
        ans = QMessageBox.question(
            self, "Confirm Delete",
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
        """Validate and persist current form.  Returns True on success."""
        if self._current_id is None:
            return False

        name        = self.f_name.text().strip()
        description = self.f_description.text().strip()
        token_url   = self.f_token_url.text().strip()
        client_id   = self.f_client_id.text().strip()
        secret      = self.f_client_secret.text()
        scope       = self.f_scope.text().strip()
        grant_type  = self.f_grant_type.currentText()
        extra_raw   = self.f_extra.toPlainText().strip()

        if not name:
            QMessageBox.warning(self, "Validation", "Name is required.")
            return False
        if not token_url:
            QMessageBox.warning(self, "Validation", "Token URL is required.")
            return False
        if not client_id:
            QMessageBox.warning(self, "Validation", "Client ID is required.")
            return False

        extra_params = {}
        if extra_raw:
            try:
                extra_params = json.loads(extra_raw)
                if not isinstance(extra_params, dict):
                    raise ValueError("must be a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                QMessageBox.warning(
                    self, "Invalid Extra Params",
                    f"Extra Params must be a valid JSON object:\n{exc}"
                )
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

    def _set_default(self):
        if self._current_id is None:
            return
        if self._dirty:
            if not self._save_client():
                return
        try:
            self.mgr.set_default(self._current_id)
            self.clients_changed.emit()
            self._refresh_list(select_id=self._current_id)
            self._set_status("✓ Set as default client", ok=True)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # ── Test connection ───────────────────────────────────────────────

    def _test_client(self):
        """Attempt to fetch a real token and display the outcome."""
        if self._current_id is None:
            return

        # Use the *form values* so the user can test before saving
        token_url   = self.f_token_url.text().strip()
        client_id   = self.f_client_id.text().strip()
        secret      = self.f_client_secret.text()
        scope       = self.f_scope.text().strip()
        grant_type  = self.f_grant_type.currentText()
        extra_raw   = self.f_extra.toPlainText().strip()

        if not token_url or not client_id:
            QMessageBox.warning(
                self, "Missing Fields",
                "Token URL and Client ID are required to test the connection."
            )
            return

        extra_params = {}
        if extra_raw:
            try:
                extra_params = json.loads(extra_raw)
            except json.JSONDecodeError:
                pass

        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing…")
        self._set_status("Connecting…", ok=None)

        # Run in a thread to avoid blocking the UI
        from PyQt6.QtCore import QThread, pyqtSignal as _sig

        class _Tester(QThread):
            done = _sig(bool, str)   # (success, message)

            def __init__(self, token_url, client_id, secret,
                         scope, grant_type, extra_params):
                super().__init__()
                self.token_url   = token_url
                self.client_id   = client_id
                self.secret      = secret
                self.scope       = scope
                self.grant_type  = grant_type
                self.extra_params = extra_params

            def run(self):
                try:
                    import httpx
                    data = {
                        "grant_type":    self.grant_type,
                        "client_id":     self.client_id,
                        "client_secret": self.secret,
                    }
                    if self.scope:
                        data["scope"] = self.scope
                    if self.grant_type == "refresh_token":
                        data["refresh_token"] = ""  # placeholder
                    data.update(self.extra_params)

                    resp = httpx.post(self.token_url, data=data, timeout=10.0)
                    if resp.status_code == 200:
                        payload = resp.json()
                        token_type = payload.get("token_type", "bearer")
                        expires_in = payload.get("expires_in")
                        has_access = bool(payload.get("access_token"))
                        msg = (
                            f"✓ Token received  [{token_type}]"
                            + (f"  expires_in={expires_in}s" if expires_in else "")
                            + ("  (no access_token!)" if not has_access else "")
                        )
                        self.done.emit(True, msg)
                    else:
                        try:
                            body = resp.json()
                            err  = body.get("error_description") or body.get("error") or resp.text[:200]
                        except Exception:
                            err = resp.text[:200]
                        self.done.emit(False, f"HTTP {resp.status_code}: {err}")
                except Exception as exc:
                    self.done.emit(False, str(exc))

        self._tester = _Tester(
            token_url, client_id, secret, scope, grant_type, extra_params
        )
        self._tester.done.connect(self._on_test_done)
        self._tester.start()

    def _on_test_done(self, success: bool, message: str):
        self.test_btn.setEnabled(True)
        self.test_btn.setText("🔌  Test Connection")
        self._set_status(message, ok=success)

    def _set_status(self, msg: str, ok: Optional[bool]):
        if ok is True:
            colour = Colors.GREEN
        elif ok is False:
            colour = Colors.RED
        else:
            colour = Colors.FG_MUTED
        self.status_label.setText(
            f"<span style='color:{colour};'>{msg}</span>"
        )

    # ── Close guard ───────────────────────────────────────────────────

    def _on_close(self):
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
                self._save_client()
        self.accept()

