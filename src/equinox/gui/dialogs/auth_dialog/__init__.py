from equinox.auth import AUTH_TYPES
from equinox.auth import AuthStrategy
from equinox.storage import Database
from equinox.storage import SavedCredentialsManager
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QFrame
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QTabWidget
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

from .oauth2.controller import OAuth2TokenController
from .tabs.api_key import ApiKeyAuthTab
from .tabs.aws import AwsSigV4AuthTab
from .tabs.basic import BasicAuthTab
from .tabs.bearer import BearerAuthTab
from .tabs.oauth2 import OAuth2AuthTab


class AuthDialog(QDialog):
    auth_configured = pyqtSignal(object)

    _AUTH_TYPE_TO_TAB = {
        "no_auth": 0,
        "basic": 1,
        "bearer": 2,
        "oauth2": 3,
        "api_key": 4,
        "aws": 5,
    }

    def __init__(self, current_auth: AuthStrategy | None = None, parent: QWidget | None = None, db: Database | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.current_auth = current_auth

        self.setWindowTitle("Configure Authentication")
        self.setMinimumSize(540, 480)
        self._init_ui()
        self._refresh_client_picker()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── Saved credential picker (above tabs) ──────────────────────
        picker_frame = QFrame()
        picker_frame.setFrameShape(QFrame.Shape.StyledPanel)
        pfl = QHBoxLayout(picker_frame)
        pfl.setContentsMargins(6, 4, 6, 4)

        pfl.addWidget(QLabel("Saved credential:"))
        self.cred_picker = QComboBox()
        self.cred_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cred_picker.setMinimumWidth(220)
        self.cred_picker.addItem("— fill in manually —", userData=None)
        self.cred_picker.currentIndexChanged.connect(self._on_client_picked)
        pfl.addWidget(self.cred_picker, 1)

        manage_btn = QPushButton("Manage Credentials…")
        manage_btn.setFlat(True)
        manage_btn.clicked.connect(self._open_client_manager)
        pfl.addWidget(manage_btn)

        layout.addWidget(picker_frame)

        self.tabs = QTabWidget()

        self.no_auth = QWidget()
        self.basic = BasicAuthTab()
        self.bearer = BearerAuthTab()
        self.oauth2 = OAuth2AuthTab()
        self.api_key = ApiKeyAuthTab()
        self.aws = AwsSigV4AuthTab()

        self.tabs.addTab(self.no_auth, "No Auth")
        self.tabs.addTab(self.basic, "Basic Auth")
        self.tabs.addTab(self.bearer, "Bearer Token")
        self.tabs.addTab(self.oauth2, "OAuth 2.0")
        self.tabs.addTab(self.api_key, "API Key")
        self.tabs.addTab(self.aws, "AWS SigV4")

        layout.addWidget(self.tabs)

        # OAuth2 controller
        self.oauth2_controller = OAuth2TokenController(self.oauth2, db=self.db, parent=self)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.clicked.connect(self._save_auth)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        layout.addLayout(btns)

    def _refresh_client_picker(self) -> None:
        """Reload the saved-credential combo from the database."""
        if not self.db:
            return
        mgr = SavedCredentialsManager(self.db)
        creds = mgr.list()

        self.cred_picker.blockSignals(True)
        current_data = self.cred_picker.currentData()
        self.cred_picker.clear()
        self.cred_picker.addItem("— fill in manually —", userData=None)
        for c in creds:
            type_label = AUTH_TYPES.get(c["auth_type"], c["auth_type"])
            label = ("★ " if c["is_default"] else "") + f"[{type_label}] {c['name']}"
            self.cred_picker.addItem(label, userData=c["id"])
            if c["id"] == current_data:
                self.cred_picker.setCurrentIndex(self.cred_picker.count() - 1)
        self.cred_picker.blockSignals(False)

        # Auto-select default if nothing was previously selected
        if self.cred_picker.currentIndex() == 0 and not self.current_auth:
            for i in range(1, self.cred_picker.count()):
                cid = self.cred_picker.itemData(i)
                if cid:
                    c = mgr.get(cid) or {}
                    if c and c.get("is_default"):
                        self.cred_picker.setCurrentIndex(i)
                        break

    def _on_client_picked(self, index: int) -> None:
        """Auto-fill fields (and switch tab) when a saved credential is selected."""
        if not self.db:
            return
        cred_id = self.cred_picker.currentData()
        if cred_id is None:
            return  # "fill in manually" selected
        cred = SavedCredentialsManager(self.db).get(cred_id)
        if not cred:
            return

        auth_type = cred["auth_type"]
        cfg = cred["config"]

        # Switch to the appropriate tab
        tab_index = self._AUTH_TYPE_TO_TAB.get(auth_type)
        if tab_index is not None:
            self.tabs.setCurrentIndex(tab_index)

        # Fill the type-specific fields
        if auth_type == "oauth2":
            self.oauth2.token_url.setText(cfg.get("token_url", ""))
            self.oauth2.client_id.setText(cfg.get("client_id", ""))
            self.oauth2.client_secret.setText(cfg.get("client_secret", ""))
            self.oauth2.scope.setText(cfg.get("scope", ""))
            self.oauth2.verify_ssl.setChecked(bool(cfg.get("verify_ssl", True)))
            ta_idx = self.oauth2.token_auth.findData(cfg.get("token_auth", "body") or "body")
            self.oauth2.token_auth.setCurrentIndex(max(ta_idx, 0))
            # Clear tokens so a fresh fetch is triggered at send time.
            # Without this, stale tokens from a previously-loaded auth
            # could remain in the form and suppress the auto-fetch.
            self.oauth2.access_token.clear()
            self.oauth2.refresh_token.clear()
        elif auth_type == "api_key":
            self.api_key.key_name.setText(cfg.get("key", "X-API-Key"))
            self.api_key.key_value.setText(cfg.get("value", ""))
            loc = cfg.get("location", "header")
            self.api_key.location.setCurrentIndex(0 if loc == "header" else 1)
        elif auth_type == "basic":
            self.basic.username.setText(cfg.get("username", ""))
            self.basic.password.setText(cfg.get("password", ""))
        elif auth_type == "bearer":
            self.bearer.token.setText(cfg.get("token", ""))
        elif auth_type == "aws_sigv4":
            self.aws.access_key.setText(cfg.get("access_key", ""))
            self.aws.secret_key.setText(cfg.get("secret_key", ""))
            self.aws.region.setText(cfg.get("region", ""))
            self.aws.service.setText(cfg.get("service", ""))
            self.aws.session_token.setText(cfg.get("session_token", ""))

    def _open_client_manager(self) -> None:
        """Open the saved-credentials manager dialog."""
        if not self.db:
            QMessageBox.information(
                self, "Not available", "The credential manager is not available in this context.",
            )
            return
        # Deferred to avoid circular import between sibling dialog modules.
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog

        dlg = SavedCredentialsDialog(self.db, self)
        dlg.credentials_changed.connect(self._refresh_client_picker)
        dlg.exec()

    def _save_auth(self) -> None:
        tab = self.tabs.currentWidget()
        if tab is None:
            return

        if hasattr(tab, "get_auth_config"):
            config = tab.get_auth_config()
            self.accept()
            self.auth_configured.emit(config)
