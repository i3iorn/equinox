"""Authentication configuration dialog"""

import json
import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QComboBox, QPushButton, QFormLayout,
    QMessageBox, QFrame, QTextEdit, QCheckBox,
)
from PyQt6.QtCore import QThread, pyqtSignal
from typing import Dict, Any, Literal, cast

from equinox.core.interpolation import VariableInterpolator, collect_interpolation_variables
from equinox.core.util.time import utc_now
from equinox.gui.theme import get_mono_font
from equinox.gui.widgets import make_secret_row

from equinox.auth import BasicAuth, OAuth2Auth, BearerAuth, APIKeyAuth, AUTH_TYPES, AWSSigV4Auth
from equinox.core.exceptions import AuthError
from equinox.storage import SavedCredentialsManager

logger = logging.getLogger(__name__)


def _sanitize_field(text: str) -> str:
    """Strip CR/LF characters that password managers may paste into fields.

    Prevents ``AuthError`` (CRLF-injection check) from firing on values
    that are merely copy-paste artefacts rather than real attacks.
    """
    return text.replace("\r", "").replace("\n", "")


# Sentinel returned by _build_auth_from_tab when a required-field check
# fails and a QMessageBox has already been shown.  Distinct from ``None``
# which means "No Auth".
_MISSING = object()


class _TokenFetchWorker(QThread):
    """Background worker that fetches an OAuth2 token without blocking the UI."""

    # Emits a dict payload: {ok, auth, error, response}
    finished = pyqtSignal(object)

    def __init__(self, auth, parent=None):
        super().__init__(parent)
        self._auth = auth

    def run(self):
        try:
            self._auth.apply(object(), {})
            self.finished.emit({
                "ok": True,
                "auth": self._auth,
                "error": None,
                "response": self._auth.last_token_response,
            })
        except Exception as exc:
            response = self._auth.last_token_response
            if response is None and isinstance(exc, AuthError):
                response = exc.details.get("token_response")
            self.finished.emit({
                "ok": False,
                "auth": self._auth,
                "error": str(exc),
                "response": response,
            })


class _TokenResponseDialog(QDialog):
    """Read-only dialog showing the redacted token endpoint response."""

    def __init__(self, data: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Token Endpoint Response")
        self.resize(560, 420)

        layout = QVBoxLayout(self)

        # Status line
        status = data.get("status_code", "?")
        method = data.get("method", "POST")
        url = data.get("url", "")
        status_lbl = QLabel(f"{method}  {url}  →  {status}")
        status_lbl.setWordWrap(True)
        layout.addWidget(status_lbl)

        # Headers section
        layout.addWidget(QLabel("Response Headers:"))
        headers_text = QTextEdit()
        headers_text.setReadOnly(True)
        headers_text.setFont(get_mono_font())
        headers_text.setMaximumHeight(120)
        headers_dict = data.get("headers", {})
        headers_text.setPlainText(
            "\n".join(f"{k}: {v}" for k, v in headers_dict.items())
        )
        layout.addWidget(headers_text)

        # Body section
        layout.addWidget(QLabel("Response Body (tokens redacted):"))
        body_text = QTextEdit()
        body_text.setReadOnly(True)
        body_text.setFont(get_mono_font())
        body = data.get("body", {})
        try:
            body_str = json.dumps(body, indent=2, ensure_ascii=False)
        except Exception:
            body_str = str(body)
        body_text.setPlainText(body_str)
        layout.addWidget(body_text)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


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
        "basic":     _TAB_BASIC,
        "bearer":    _TAB_BEARER,
        "oauth2":    _TAB_OAUTH2,
        "api_key":   _TAB_APIKEY,
        "aws_sigv4": _TAB_AWS,
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
        return lbl

    def _create_no_auth_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("No authentication will be used for this request."))
        lay.addStretch()
        return w

    def _create_basic_auth_tab(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        self.basic_username = QLineEdit()
        self.basic_password = QLineEdit()
        self.basic_password.setEchoMode(QLineEdit.EchoMode.Password)
        lay.addRow("Username:", self.basic_username)
        lay.addRow("Password:", make_secret_row(self.basic_password))
        lay.addRow(self._info("Credentials sent base64-encoded in the Authorization header."))
        return w

    def _create_bearer_auth_tab(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        self.bearer_token = QLineEdit()
        self.bearer_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.bearer_token.setPlaceholderText("Paste your bearer token here…")
        lay.addRow("Token:", make_secret_row(self.bearer_token))
        lay.addRow(self._info("Sent as:  Authorization: Bearer <token>"))
        return w

    def _create_oauth2_tab(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
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
        self.oauth2_token_auth    = QComboBox()
        self.oauth2_token_auth.addItem("Body (RFC 6749 default)", userData="body")
        self.oauth2_token_auth.addItem("HTTP Basic Auth (D&B Direct+, GitHub…)", userData="basic")
        self.oauth2_token_auth.setToolTip(
            "How client credentials are sent to the token endpoint.\n"
            "• Body — client_id/client_secret in the POST body (default, RFC 6749 §2.3.1)\n"
            "• Basic — Base64-encoded Authorization header (required by some providers)"
        )
        self.oauth2_verify_ssl_check = QCheckBox("Verify token endpoint SSL certificates")
        self.oauth2_verify_ssl_check.setChecked(True)

        lay.addRow("Token URL:*",     self.oauth2_token_url)
        lay.addRow("Client ID:*",     self.oauth2_client_id)
        lay.addRow("Client Secret:",  make_secret_row(self.oauth2_client_secret))
        lay.addRow("Scope:",          self.oauth2_scope)
        lay.addRow("Client Auth:",    self.oauth2_token_auth)
        lay.addRow("",                 self.oauth2_verify_ssl_check)
        lay.addRow("Access Token:",   make_secret_row(self.oauth2_access_token))
        lay.addRow("Refresh Token:",  make_secret_row(self.oauth2_refresh_token))
        lay.addRow(self._info(
            "Select a saved credential above to auto-fill, or type manually.\n"
            "Uses client_credentials or refresh_token grant type."
        ))

        # ── Token fetch ───────────────────────────────────────────────
        self.oauth2_fetch_btn = QPushButton("Fetch Token…")
        self.oauth2_fetch_btn.clicked.connect(self._fetch_oauth2_token)
        self.oauth2_view_response_btn = QPushButton("View Response…")
        self.oauth2_view_response_btn.setEnabled(False)
        self.oauth2_view_response_btn.setToolTip("Inspect the token endpoint response (tokens redacted)")
        self.oauth2_view_response_btn.clicked.connect(self._view_token_response)
        self._last_fetched_auth = None  # stores OAuth2Auth after successful fetch
        self._last_token_response = None
        self._fetch_requested_token_auth = "body"
        fetch_row = QHBoxLayout()
        fetch_row.addWidget(self.oauth2_fetch_btn)
        fetch_row.addWidget(self.oauth2_view_response_btn)
        fetch_row.addStretch()
        fetch_container = QWidget()
        fetch_container.setLayout(fetch_row)
        lay.addRow("", fetch_container)

        self.oauth2_fetch_status = QLabel("")
        self.oauth2_fetch_status.setWordWrap(True)
        lay.addRow(self.oauth2_fetch_status)

        return w

    def _create_api_key_tab(self) -> QWidget:
        w = QWidget()
        lay = QFormLayout(w)
        self.api_key_name     = QLineEdit()
        self.api_key_name.setPlaceholderText("X-API-Key")
        self.api_key_value    = QLineEdit()
        self.api_key_value.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_value.setPlaceholderText("your-api-key-value")
        self.api_key_location = QComboBox()
        self.api_key_location.addItems(["header", "query"])
        lay.addRow("Key Name:",  self.api_key_name)
        lay.addRow("Key Value:", make_secret_row(self.api_key_value))
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
        lay.addRow("Secret Access Key:", make_secret_row(self.aws_secret_key))
        lay.addRow("Region:", self.aws_region)
        lay.addRow("Service:", self.aws_service)
        lay.addRow("Session Token:", make_secret_row(self.aws_session_token))
        lay.addRow(self._info(
            "Signs requests using AWS Signature Version 4.  "
            "Leave Session Token blank unless you are using temporary credentials."
        ))
        return w

    # ── Variable interpolation helper ─────────────────────────────────

    def _collect_variables(self) -> Dict[str, str]:
        """Gather interpolation variables from the database.

        Returns an empty dict when the database is not available (e.g.
        the dialog was opened without a DB context), so callers can
        skip interpolation gracefully.
        """
        if not self._db:
            return {}
        try:
            return collect_interpolation_variables(self._db)
        except Exception as exc:
            logger.debug("Failed to collect interpolation variables: %s", exc)
            return {}

    # ── OAuth2 token fetch ─────────────────────────────────────────────


    def _fetch_oauth2_token(self) -> None:
        """Fetch an OAuth2 token in a background thread and update the form."""
        token_url = self.oauth2_token_url.text().strip()
        client_id = self.oauth2_client_id.text().strip()
        if not token_url or not client_id:
            self.oauth2_fetch_status.setText("Token URL and Client ID are required.")
            return

        # Interpolate {{VAR}} placeholders in OAuth2 fields so the user can
        # reference environment/collection variables in token URLs, client IDs, etc.
        variables = self._collect_variables()
        if variables:
            try:
                _interp = lambda s: VariableInterpolator.interpolate(s, variables)
                token_url = _interp(token_url)
                client_id = _interp(client_id)
                client_secret = _interp(self.oauth2_client_secret.text().strip()) or None
                scope = _interp(self.oauth2_scope.text().strip()) or None
            except Exception as exc:
                logger.warning("Variable interpolation failed in OAuth2 fetch: %s", exc)
                self.oauth2_fetch_status.setText(f"Variable error: {exc}")
                return
        else:
            client_secret = self.oauth2_client_secret.text().strip() or None
            scope = self.oauth2_scope.text().strip() or None

        token_auth: Literal["body", "basic"] = (
            "basic" if str(self.oauth2_token_auth.currentData() or "body") == "basic" else "body"
        )
        self._fetch_requested_token_auth = token_auth
        auth = OAuth2Auth(
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            verify_ssl=self.oauth2_verify_ssl_check.isChecked(),
            token_auth=token_auth,
        )
        self.oauth2_fetch_btn.setEnabled(False)
        self.oauth2_view_response_btn.setEnabled(False)
        self._last_token_response = None
        self.oauth2_fetch_status.setText("Fetching…")

        # Store as instance attribute to prevent garbage-collection mid-run
        self._fetch_worker = _TokenFetchWorker(auth, self)
        self._fetch_worker.finished.connect(self._on_token_fetched)
        self._fetch_worker.start()

    def _on_token_fetched(self, result: object) -> None:
        """Handle the result of :class:`_TokenFetchWorker`."""
        self.oauth2_fetch_btn.setEnabled(True)
        if not isinstance(result, dict):
            self.oauth2_fetch_status.setText(f"Error: {result}")
            return

        auth = result.get("auth")
        if isinstance(auth, OAuth2Auth):
            self._last_fetched_auth = auth
            self._last_token_response = result.get("response") or auth.last_token_response
        else:
            self._last_fetched_auth = None
            self._last_token_response = result.get("response")

        self.oauth2_view_response_btn.setEnabled(self._last_token_response is not None)

        if not result.get("ok"):
            self.oauth2_fetch_status.setText(f"Error: {result.get('error', 'Unknown error')}")
            return

        if self._last_fetched_auth is not None:
            auth = self._last_fetched_auth
            # Back-fill the access/refresh token fields so the user can inspect them
            self.oauth2_access_token.setText(auth.access_token or "")
            if auth.refresh_token:
                self.oauth2_refresh_token.setText(auth.refresh_token)

            info = auth.get_token_info()
            expiry = ""
            if info.get("expires_at"):
                from datetime import datetime
                try:
                    secs = int(
                        (datetime.fromisoformat(str(info["expires_at"])) -
                         utc_now()).total_seconds()
                    )
                    expiry = f", expires in {secs}s"
                except Exception:
                    pass
            preview = info.get("access_token", "")
            message = f"Token acquired{expiry}  [{preview}]"

            selected_mode = str(self._fetch_requested_token_auth or "body")
            effective_mode = str(getattr(auth, "token_auth", selected_mode) or selected_mode)
            if effective_mode != selected_mode:
                mode_idx = self.oauth2_token_auth.findData(effective_mode)
                if mode_idx >= 0:
                    self.oauth2_token_auth.setCurrentIndex(mode_idx)
                mode_label = "HTTP Basic" if effective_mode == "basic" else "Body"
                message = (
                    f"{message}\n"
                    f"Hint: token endpoint accepted {mode_label} client auth. "
                    "Client Auth was updated for this form; click Save to persist it."
                )

            self.oauth2_fetch_status.setText(message)

    def _view_token_response(self) -> None:
        """Open a dialog showing the redacted token endpoint response."""
        response = self._last_token_response
        if response is None:
            return

        dlg = _TokenResponseDialog(response, self)
        dlg.exec()

    # ── Saved credential picker ────────────────────────────────────────

    def _refresh_client_picker(self) -> None:
        """Reload the saved-credential combo from the database."""
        if not self._db:
            return
        mgr = SavedCredentialsManager(self._db)
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
                    c = mgr.get(cid)
                    if c and c.get("is_default"):
                        self.cred_picker.setCurrentIndex(i)
                        break

    def _on_client_picked(self, index: int) -> None:
        """Auto-fill fields (and switch tab) when a saved credential is selected."""
        if not self._db:
            return
        cred_id = self.cred_picker.currentData()
        if cred_id is None:
            return   # "fill in manually" selected
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
            self.oauth2_verify_ssl_check.setChecked(bool(cfg.get("verify_ssl", True)))
            ta_idx = self.oauth2_token_auth.findData(cfg.get("token_auth", "body") or "body")
            self.oauth2_token_auth.setCurrentIndex(max(ta_idx, 0))
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

    def _open_client_manager(self) -> None:
        """Open the saved-credentials manager dialog."""
        if not self._db:
            QMessageBox.information(
                self, "Not available",
                "The credential manager is not available in this context."
            )
            return
        # Deferred to avoid circular import between sibling dialog modules.
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog
        dlg = SavedCredentialsDialog(self._db, self)
        dlg.credentials_changed.connect(self._refresh_client_picker)
        dlg.exec()

    # ── Load / Save ───────────────────────────────────────────────────

    def _load_current_auth(self) -> None:
        if not self.current_auth:
            self.tabs.setCurrentIndex(self._TAB_NONE)
            return
        if isinstance(self.current_auth, BasicAuth):
            self.tabs.setCurrentIndex(self._TAB_BASIC)
            self.basic_username.setText(self.current_auth.username)
            self.basic_password.setText(self.current_auth.password)
        elif isinstance(self.current_auth, BearerAuth):
            self.tabs.setCurrentIndex(self._TAB_BEARER)
            self.bearer_token.setText(self.current_auth.token)
        elif isinstance(self.current_auth, OAuth2Auth):
            self.tabs.setCurrentIndex(self._TAB_OAUTH2)
            self.oauth2_token_url.setText(self.current_auth.token_url or "")
            self.oauth2_client_id.setText(self.current_auth.client_id or "")
            self.oauth2_client_secret.setText(self.current_auth.client_secret or "")
            self.oauth2_scope.setText(self.current_auth.scope or "")
            self.oauth2_verify_ssl_check.setChecked(getattr(self.current_auth, "verify_ssl", True))
            self.oauth2_access_token.setText(self.current_auth.access_token or "")
            self.oauth2_refresh_token.setText(self.current_auth.refresh_token or "")
            ta_idx = self.oauth2_token_auth.findData(
                getattr(self.current_auth, "token_auth", "body") or "body"
            )
            self.oauth2_token_auth.setCurrentIndex(max(ta_idx, 0))
        elif isinstance(self.current_auth, APIKeyAuth):
            self.tabs.setCurrentIndex(self._TAB_APIKEY)
            self.api_key_name.setText(self.current_auth.key)
            self.api_key_value.setText(self.current_auth.value)
            self.api_key_location.setCurrentIndex(
                0 if self.current_auth.location == "header" else 1
            )
        elif isinstance(self.current_auth, AWSSigV4Auth):
            self.tabs.setCurrentIndex(self._TAB_AWS)
            self.aws_access_key.setText(self.current_auth.access_key)
            self.aws_secret_key.setText(self.current_auth.secret_key)
            self.aws_region.setText(self.current_auth.region)
            self.aws_service.setText(self.current_auth.service)
            self.aws_session_token.setText(self.current_auth.session_token or "")

    def _save_auth(self) -> None:
        tab = self.tabs.currentIndex()

        try:
            auth = self._build_auth_from_tab(tab)
        except AuthError as exc:
            QMessageBox.warning(
                self, "Invalid Credentials",
                f"Could not save authentication:\n{exc}",
            )
            return

        if auth is _MISSING:
            # _build_auth_from_tab returned sentinel — validation message already shown
            return

        try:
            self._saved_auth = auth
            self.auth_configured.emit(auth)
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to configure authentication: {exc}")

    def _build_auth_from_tab(self, tab: int):
        """Construct an auth strategy from the current tab's fields.

        Returns:
            An auth strategy object, ``None`` (No Auth), or :data:`_MISSING`
            when a required-field check fails (message already shown).

        Raises:
            AuthError: When credential validation fails (CRLF, length, etc.).
        """
        if tab == self._TAB_NONE:
            return None

        if tab == self._TAB_BASIC:
            username = _sanitize_field(self.basic_username.text().strip())
            password = _sanitize_field(self.basic_password.text())
            if not username or not password:
                QMessageBox.warning(self, "Missing Fields", "Enter both username and password.")
                return _MISSING
            return BasicAuth(username=username, password=password)

        if tab == self._TAB_BEARER:
            token = _sanitize_field(self.bearer_token.text().strip())
            if not token:
                QMessageBox.warning(self, "Missing Token", "Enter a bearer token.")
                return _MISSING
            return BearerAuth(token=token)

        if tab == self._TAB_OAUTH2:
            token_url     = _sanitize_field(self.oauth2_token_url.text().strip())
            client_id     = _sanitize_field(self.oauth2_client_id.text().strip())
            client_secret = _sanitize_field(self.oauth2_client_secret.text().strip())
            if not token_url or not client_id:
                QMessageBox.warning(self, "Missing Fields",
                                    "Token URL and Client ID are required.")
                return _MISSING
            token_auth: Literal["body", "basic"] = (
                "basic" if str(self.oauth2_token_auth.currentData() or "body") == "basic" else "body"
            )
            auth = OAuth2Auth(
                token_url=token_url,
                client_id=client_id,
                client_secret=client_secret,
                scope=_sanitize_field(self.oauth2_scope.text().strip()) or None,
                access_token=_sanitize_field(self.oauth2_access_token.text().strip()) or None,
                refresh_token=_sanitize_field(self.oauth2_refresh_token.text().strip()) or None,
                verify_ssl=self.oauth2_verify_ssl_check.isChecked(),
                token_auth=token_auth,
            )
            # Carry forward expires_at from a successful "Fetch Token…"
            # so the token isn't treated as eternal.
            if self._last_fetched_auth is not None:
                fetched = self._last_fetched_auth
                if (
                    fetched.expires_at is not None
                    and auth.access_token == fetched.access_token
                ):
                    auth.expires_at = fetched.expires_at
            return auth

        if tab == self._TAB_APIKEY:
            key_name  = _sanitize_field(self.api_key_name.text().strip())
            key_value = _sanitize_field(self.api_key_value.text().strip())
            if not key_name or not key_value:
                QMessageBox.warning(self, "Missing Fields", "Enter both key name and value.")
                return _MISSING
            location: Literal["header", "query"] = (
                "header" if self.api_key_location.currentIndex() == 0 else "query"
            )
            return APIKeyAuth(
                key=key_name, value=key_value,
                location=cast(Literal["header", "query"], location),
            )

        if tab == self._TAB_AWS:
            access_key = _sanitize_field(self.aws_access_key.text().strip())
            secret_key = _sanitize_field(self.aws_secret_key.text().strip())
            region     = _sanitize_field(self.aws_region.text().strip())
            service    = _sanitize_field(self.aws_service.text().strip())
            if not access_key or not secret_key or not region or not service:
                QMessageBox.warning(self, "Missing Fields",
                                    "Access Key, Secret Key, Region and Service are required.")
                return _MISSING
            return AWSSigV4Auth(
                access_key=access_key,
                secret_key=secret_key,
                region=region,
                service=service,
                session_token=_sanitize_field(self.aws_session_token.text().strip()) or None,
            )

        return None

    @staticmethod
    def configure_auth(current_auth=None, parent=None, db=None) -> tuple:
        dialog = AuthDialog(current_auth, parent, db=db)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return (True, getattr(dialog, "_saved_auth", None))
        return (False, current_auth)
