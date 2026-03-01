"""Authentication configuration dialog"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QComboBox, QPushButton, QFormLayout,
    QMessageBox, QToolButton, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from typing import Optional

from equinox.gui.theme import Colors

from equinox.auth import BasicAuth, OAuth2Auth, BearerAuth, APIKeyAuth


class _TokenFetchWorker(QThread):
    """Background worker that fetches an OAuth2 token without blocking the UI."""

    finished = pyqtSignal(object)  # emits OAuth2Auth on success, str on error

    def __init__(self, auth, parent=None):
        super().__init__(parent)
        self._auth = auth

    def run(self):
        try:
            # apply() triggers _refresh_access_token(); OAuth2Auth.apply() only
            # reads self.* fields — it never reads the request argument.
            self._auth.apply(object(), {})
            self.finished.emit(self._auth)
        except Exception as exc:
            self.finished.emit(str(exc))


def _make_secret_row(line_edit: QLineEdit) -> QHBoxLayout:
    """Wrap a password QLineEdit with a show/hide toggle button."""
    row = QHBoxLayout()
    row.setSpacing(2)
    row.addWidget(line_edit, 1)
    toggle = QToolButton()
    toggle.setCheckable(True)
    toggle.setText("👁")
    toggle.setFixedWidth(28)
    toggle.setToolTip("Show / hide")
    toggle.setStyleSheet("QToolButton { border: none; font-size: 14px; }")
    toggle.toggled.connect(
        lambda checked: line_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
    )
    row.addWidget(toggle)
    return row


class AuthDialog(QDialog):
    """Dialog for configuring request authentication.

    A *Saved credential* picker at the top lets the user select any named
    credential of any type (OAuth2, API Key, Basic, Bearer) and auto-fill
    the appropriate tab.
    """

    auth_configured = pyqtSignal(object)

    # Tab index constants
    _TAB_NONE   = 0
    _TAB_BASIC  = 1
    _TAB_BEARER = 2
    _TAB_OAUTH2 = 3
    _TAB_APIKEY = 4
    _TAB_AWS    = 5

    _AUTH_TYPE_TO_TAB = {
        "basic":     1,
        "bearer":    2,
        "oauth2":    3,
        "api_key":   4,
        "aws_sigv4": 5,
    }

    def __init__(self, current_auth=None, parent=None, db=None):
        super().__init__(parent)
        self.current_auth = current_auth
        self._db = db   # optional — enables the saved-credential picker

        self.setWindowTitle("Configure Authentication")
        self.setMinimumSize(540, 480)
        self._init_ui()
        self._load_current_auth()

        # Populate credential picker once at startup
        if self._db:
            self._refresh_client_picker()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ── Saved credential picker (above tabs) ──────────────────────
        picker_frame = QFrame()
        picker_frame.setFrameShape(QFrame.Shape.StyledPanel)
        pfl = QHBoxLayout(picker_frame)
        pfl.setContentsMargins(6, 4, 6, 4)

        pfl.addWidget(QLabel("Saved credential:"))
        self.cred_picker = QComboBox()
        self.cred_picker.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.cred_picker.setMinimumWidth(220)
        self.cred_picker.addItem("— fill in manually —", userData=None)
        self.cred_picker.currentIndexChanged.connect(self._on_client_picked)
        pfl.addWidget(self.cred_picker, 1)

        manage_btn = QPushButton("Manage Credentials…")
        manage_btn.setFlat(True)
        manage_btn.setStyleSheet(f"color: {Colors.BLUE}; text-decoration: underline;")
        manage_btn.clicked.connect(self._open_client_manager)
        pfl.addWidget(manage_btn)

        layout.addWidget(picker_frame)

        # ── Auth type tabs ─────────────────────────────────────────────
        self.tabs = QTabWidget()

        self.no_auth_tab      = self._create_no_auth_tab()
        self.basic_auth_tab   = self._create_basic_auth_tab()
        self.bearer_auth_tab  = self._create_bearer_auth_tab()
        self.oauth2_tab       = self._create_oauth2_tab()
        self.api_key_tab      = self._create_api_key_tab()
        self.aws_tab          = self._create_aws_tab()

        self.tabs.addTab(self.no_auth_tab,     "No Auth")
        self.tabs.addTab(self.basic_auth_tab,  "Basic Auth")
        self.tabs.addTab(self.bearer_auth_tab, "Bearer Token")
        self.tabs.addTab(self.oauth2_tab,      "OAuth 2.0")
        self.tabs.addTab(self.api_key_tab,     "API Key")
        self.tabs.addTab(self.aws_tab,         "AWS SigV4")

        layout.addWidget(self.tabs)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_auth)
        save_btn.setDefault(True)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    # ── Tab builders ──────────────────────────────────────────────────

    def _info(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {Colors.FG_MUTED};")
        return lbl

    def _create_no_auth_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        lay.addWidget(QLabel("No authentication will be used for this request."))
        lay.addStretch()
        return w

    def _create_basic_auth_tab(self) -> QWidget:
        w = QWidget(); lay = QFormLayout(w)
        self.basic_username = QLineEdit()
        self.basic_password = QLineEdit()
        self.basic_password.setEchoMode(QLineEdit.EchoMode.Password)
        lay.addRow("Username:", self.basic_username)
        lay.addRow("Password:", _make_secret_row(self.basic_password))
        lay.addRow(self._info("Credentials sent base64-encoded in the Authorization header."))
        return w

    def _create_bearer_auth_tab(self) -> QWidget:
        w = QWidget(); lay = QFormLayout(w)
        self.bearer_token = QLineEdit()
        self.bearer_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.bearer_token.setPlaceholderText("Paste your bearer token here…")
        lay.addRow("Token:", _make_secret_row(self.bearer_token))
        lay.addRow(self._info("Sent as:  Authorization: Bearer <token>"))
        return w

    def _create_oauth2_tab(self) -> QWidget:
        w = QWidget(); lay = QFormLayout(w)
        lay.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.oauth2_token_url     = QLineEdit()
        self.oauth2_token_url.setPlaceholderText("https://auth.example.com/oauth/token")
        self.oauth2_client_id     = QLineEdit()
        self.oauth2_client_id.setPlaceholderText("your-client-id")
        self.oauth2_client_secret = QLineEdit()
        self.oauth2_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.oauth2_scope         = QLineEdit()
        self.oauth2_scope.setPlaceholderText("read write  (optional)")
        self.oauth2_access_token  = QLineEdit()
        self.oauth2_access_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.oauth2_access_token.setPlaceholderText("Existing access token  (optional)")
        self.oauth2_refresh_token = QLineEdit()
        self.oauth2_refresh_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.oauth2_refresh_token.setPlaceholderText("Refresh token  (optional)")

        lay.addRow("Token URL:*",     self.oauth2_token_url)
        lay.addRow("Client ID:*",     self.oauth2_client_id)
        lay.addRow("Client Secret:",  _make_secret_row(self.oauth2_client_secret))
        lay.addRow("Scope:",          self.oauth2_scope)
        lay.addRow("Access Token:",   _make_secret_row(self.oauth2_access_token))
        lay.addRow("Refresh Token:",  _make_secret_row(self.oauth2_refresh_token))
        lay.addRow(self._info(
            "Select a saved credential above to auto-fill, or type manually.\n"
            "Uses client_credentials or refresh_token grant type."
        ))

        # ── Token fetch ───────────────────────────────────────────────
        self.oauth2_fetch_btn = QPushButton("Fetch Token…")
        self.oauth2_fetch_btn.clicked.connect(self._fetch_oauth2_token)
        fetch_row = QHBoxLayout()
        fetch_row.addWidget(self.oauth2_fetch_btn)
        fetch_row.addStretch()
        fetch_container = QWidget()
        fetch_container.setLayout(fetch_row)
        lay.addRow("", fetch_container)

        self.oauth2_fetch_status = QLabel("")
        self.oauth2_fetch_status.setWordWrap(True)
        lay.addRow(self.oauth2_fetch_status)

        return w

    def _create_api_key_tab(self) -> QWidget:
        w = QWidget(); lay = QFormLayout(w)
        self.api_key_name     = QLineEdit()
        self.api_key_name.setPlaceholderText("X-API-Key")
        self.api_key_value    = QLineEdit()
        self.api_key_value.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_value.setPlaceholderText("your-api-key-value")
        self.api_key_location = QComboBox()
        self.api_key_location.addItems(["header", "query"])
        lay.addRow("Key Name:",  self.api_key_name)
        lay.addRow("Key Value:", _make_secret_row(self.api_key_value))
        lay.addRow("Add To:",    self.api_key_location)
        lay.addRow(self._info("API key can be sent as a header or query parameter."))
        return w

    def _create_aws_tab(self) -> QWidget:
        """AWS Signature Version 4 auth tab."""
        w = QWidget()
        lay = QFormLayout(w)
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
        self.aws_session_token.setPlaceholderText("Optional — for temporary credentials (STS)")
        lay.addRow("Access Key ID:", self.aws_access_key)
        lay.addRow("Secret Access Key:", _make_secret_row(self.aws_secret_key))
        lay.addRow("Region:", self.aws_region)
        lay.addRow("Service:", self.aws_service)
        lay.addRow("Session Token:", _make_secret_row(self.aws_session_token))
        lay.addRow(self._info(
            "Signs requests using AWS Signature Version 4.  "
            "Leave Session Token blank unless you are using temporary credentials."
        ))
        return w

    # ── OAuth2 token fetch ─────────────────────────────────────────────

    def _fetch_oauth2_token(self) -> None:
        """Fetch an OAuth2 token in a background thread and update the form."""
        token_url = self.oauth2_token_url.text().strip()
        client_id = self.oauth2_client_id.text().strip()
        if not token_url or not client_id:
            self.oauth2_fetch_status.setText("Token URL and Client ID are required.")
            self.oauth2_fetch_status.setStyleSheet(f"color: {Colors.RED};")
            return

        auth = OAuth2Auth(
            token_url=token_url,
            client_id=client_id,
            client_secret=self.oauth2_client_secret.text().strip() or None,
            scope=self.oauth2_scope.text().strip() or None,
        )
        self.oauth2_fetch_btn.setEnabled(False)
        self.oauth2_fetch_status.setText("Fetching…")
        self.oauth2_fetch_status.setStyleSheet(f"color: {Colors.FG_MUTED};")

        # Store as instance attribute to prevent garbage-collection mid-run
        self._fetch_worker = _TokenFetchWorker(auth, self)
        self._fetch_worker.finished.connect(self._on_token_fetched)
        self._fetch_worker.start()

    def _on_token_fetched(self, result: object) -> None:
        """Handle the result of :class:`_TokenFetchWorker`."""
        self.oauth2_fetch_btn.setEnabled(True)
        if isinstance(result, str):
            # Error message
            self.oauth2_fetch_status.setText(f"Error: {result}")
            self.oauth2_fetch_status.setStyleSheet(f"color: {Colors.RED};")
        else:
            auth: OAuth2Auth = result
            # Back-fill the access/refresh token fields so the user can inspect them
            self.oauth2_access_token.setText(auth.access_token or "")
            if auth.refresh_token:
                self.oauth2_refresh_token.setText(auth.refresh_token)

            info = auth.get_token_info()
            expiry = ""
            if info.get("expires_at"):
                from datetime import datetime, timezone
                try:
                    secs = int(
                        (datetime.fromisoformat(str(info["expires_at"])) -
                         datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
                    )
                    expiry = f", expires in {secs}s"
                except Exception:
                    pass
            preview = info.get("access_token", "")
            self.oauth2_fetch_status.setText(
                f"Token acquired{expiry}  [{preview}]"
            )
            self.oauth2_fetch_status.setStyleSheet(f"color: {Colors.GREEN};")

    # ── Saved credential picker ────────────────────────────────────────

    def _refresh_client_picker(self):
        """Reload the saved-credential combo from the database."""
        if not self._db:
            return
        from equinox.storage import SavedCredentialsManager
        from equinox.storage.saved_credentials import AUTH_TYPE_LABELS
        mgr = SavedCredentialsManager(self._db)
        creds = mgr.list()

        self.cred_picker.blockSignals(True)
        current_data = self.cred_picker.currentData()
        self.cred_picker.clear()
        self.cred_picker.addItem("— fill in manually —", userData=None)
        for c in creds:
            type_label = AUTH_TYPE_LABELS.get(c["auth_type"], c["auth_type"])
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
                    c = mgr.get(cid)
                    if c and c.get("is_default"):
                        self.cred_picker.setCurrentIndex(i)
                        break

    def _on_client_picked(self, index: int):
        """Auto-fill fields (and switch tab) when a saved credential is selected."""
        if not self._db:
            return
        cred_id = self.cred_picker.currentData()
        if cred_id is None:
            return   # "fill in manually" selected
        from equinox.storage import SavedCredentialsManager
        cred = SavedCredentialsManager(self._db).get(cred_id)
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
            self.oauth2_token_url.setText(cfg.get("token_url", ""))
            self.oauth2_client_id.setText(cfg.get("client_id", ""))
            self.oauth2_client_secret.setText(cfg.get("client_secret", ""))
            self.oauth2_scope.setText(cfg.get("scope", ""))
            # Clear tokens so a fresh fetch is triggered at send time.
            # Without this, stale tokens from a previously-loaded auth
            # could remain in the form and suppress the auto-fetch.
            self.oauth2_access_token.clear()
            self.oauth2_refresh_token.clear()
        elif auth_type == "api_key":
            self.api_key_name.setText(cfg.get("key", "X-API-Key"))
            self.api_key_value.setText(cfg.get("value", ""))
            loc = cfg.get("location", "header")
            self.api_key_location.setCurrentIndex(0 if loc == "header" else 1)
        elif auth_type == "basic":
            self.basic_username.setText(cfg.get("username", ""))
            self.basic_password.setText(cfg.get("password", ""))
        elif auth_type == "bearer":
            self.bearer_token.setText(cfg.get("token", ""))
        elif auth_type == "aws_sigv4":
            self.aws_access_key.setText(cfg.get("access_key", ""))
            self.aws_secret_key.setText(cfg.get("secret_key", ""))
            self.aws_region.setText(cfg.get("region", ""))
            self.aws_service.setText(cfg.get("service", ""))
            self.aws_session_token.setText(cfg.get("session_token", ""))

    def _open_client_manager(self):
        """Open the saved-credentials manager dialog."""
        if not self._db:
            QMessageBox.information(
                self, "Not available",
                "The credential manager is not available in this context."
            )
            return
        from equinox.gui.saved_credentials_dialog import SavedCredentialsDialog
        dlg = SavedCredentialsDialog(self._db, self)
        dlg.credentials_changed.connect(self._refresh_client_picker)
        dlg.exec()

    # ── Load / Save ───────────────────────────────────────────────────

    def _load_current_auth(self):
        from equinox.auth.aws_sigv4 import AWSSigV4Auth
        if not self.current_auth:
            self.tabs.setCurrentIndex(0)
            return
        if isinstance(self.current_auth, BasicAuth):
            self.tabs.setCurrentIndex(1)
            self.basic_username.setText(self.current_auth.username)
            self.basic_password.setText(self.current_auth.password)
        elif isinstance(self.current_auth, BearerAuth):
            self.tabs.setCurrentIndex(2)
            self.bearer_token.setText(self.current_auth.token)
        elif isinstance(self.current_auth, OAuth2Auth):
            self.tabs.setCurrentIndex(3)
            self.oauth2_token_url.setText(self.current_auth.token_url or "")
            self.oauth2_client_id.setText(self.current_auth.client_id or "")
            self.oauth2_client_secret.setText(self.current_auth.client_secret or "")
            self.oauth2_scope.setText(self.current_auth.scope or "")
            self.oauth2_access_token.setText(self.current_auth.access_token or "")
            self.oauth2_refresh_token.setText(self.current_auth.refresh_token or "")
        elif isinstance(self.current_auth, APIKeyAuth):
            self.tabs.setCurrentIndex(4)
            self.api_key_name.setText(self.current_auth.key)
            self.api_key_value.setText(self.current_auth.value)
            self.api_key_location.setCurrentIndex(
                0 if self.current_auth.location == "header" else 1
            )
        elif isinstance(self.current_auth, AWSSigV4Auth):
            self.tabs.setCurrentIndex(5)
            self.aws_access_key.setText(self.current_auth.access_key)
            self.aws_secret_key.setText(self.current_auth.secret_key)
            self.aws_region.setText(self.current_auth.region)
            self.aws_service.setText(self.current_auth.service)
            self.aws_session_token.setText(self.current_auth.session_token or "")

    def _save_auth(self):
        try:
            tab = self.tabs.currentIndex()
            if tab == 0:
                auth = None
            elif tab == 1:
                username = self.basic_username.text().strip()
                password = self.basic_password.text()
                if not username or not password:
                    QMessageBox.warning(self, "Missing Fields", "Enter both username and password.")
                    return
                auth = BasicAuth(username=username, password=password)
            elif tab == 2:
                token = self.bearer_token.text().strip()
                if not token:
                    QMessageBox.warning(self, "Missing Token", "Enter a bearer token.")
                    return
                auth = BearerAuth(token=token)
            elif tab == 3:
                token_url     = self.oauth2_token_url.text().strip()
                client_id     = self.oauth2_client_id.text().strip()
                client_secret = self.oauth2_client_secret.text().strip()
                if not token_url or not client_id:
                    QMessageBox.warning(self, "Missing Fields",
                                        "Token URL and Client ID are required.")
                    return
                auth = OAuth2Auth(
                    token_url=token_url,
                    client_id=client_id,
                    client_secret=client_secret,
                    scope=self.oauth2_scope.text().strip() or None,
                    access_token=self.oauth2_access_token.text().strip() or None,
                    refresh_token=self.oauth2_refresh_token.text().strip() or None,
                )
            elif tab == 4:
                key_name  = self.api_key_name.text().strip()
                key_value = self.api_key_value.text().strip()
                if not key_name or not key_value:
                    QMessageBox.warning(self, "Missing Fields", "Enter both key name and value.")
                    return
                auth = APIKeyAuth(
                    key=key_name, value=key_value,
                    location=self.api_key_location.currentText(),
                )
            elif tab == 5:
                from equinox.auth.aws_sigv4 import AWSSigV4Auth
                access_key = self.aws_access_key.text().strip()
                secret_key = self.aws_secret_key.text().strip()
                region     = self.aws_region.text().strip()
                service    = self.aws_service.text().strip()
                if not access_key or not secret_key or not region or not service:
                    QMessageBox.warning(self, "Missing Fields",
                                        "Access Key, Secret Key, Region and Service are required.")
                    return
                auth = AWSSigV4Auth(
                    access_key=access_key,
                    secret_key=secret_key,
                    region=region,
                    service=service,
                    session_token=self.aws_session_token.text().strip() or None,
                )
            else:
                auth = None

            self._saved_auth = auth
            self.auth_configured.emit(auth)
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to configure authentication: {exc}")

    @staticmethod
    def configure_auth(current_auth=None, parent=None, db=None) -> tuple:
        dialog = AuthDialog(current_auth, parent, db=db)
        result = None

        def on_auth_configured(auth):
            nonlocal result
            result = auth

        dialog.auth_configured.connect(on_auth_configured)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return (True, result)
        return (False, current_auth)
