import logging
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from equinox.auth import AUTH_TYPES, AuthStrategy
from equinox.storage import Database, SavedCredentialsManager

from .oauth2.controller import OAuth2TokenController
from .tabs.api_key import ApiKeyAuthTab
from .tabs.aws import AwsSigV4AuthTab
from .tabs.basic import BasicAuthTab
from .tabs.bearer import BearerAuthTab
from .tabs.oauth2 import OAuth2AuthTab

LOGGER = logging.getLogger(__name__)


class AuthType:
    """Supported authentication type identifiers."""

    NO_AUTH = "no_auth"
    BASIC = "basic"
    BEARER = "bearer"
    OAUTH2 = "oauth2"
    API_KEY = "api_key"  # pragma: allowlist secret
    AWS_SIGV4 = "aws_sigv4"


class ApiKeyLocation:
    """Supported API key locations."""

    HEADER = "header"
    QUERY = "query"


class AuthConfigKey:
    """Saved authentication configuration keys."""

    ACCESS_KEY = "access_key"
    AUTH_TYPE = "auth_type"
    CLIENT_ID = "client_id"
    CLIENT_SECRET = "client_secret"  # pragma: allowlist secret
    CONFIG = "config"
    ID = "id"
    IS_DEFAULT = "is_default"
    KEY = "key"
    LOCATION = "location"
    NAME = "name"
    PASSWORD = "password"  # pragma: allowlist secret
    REGION = "region"
    SCOPE = "scope"
    SECRET_KEY = "secret_key"  # pragma: allowlist secret
    SERVICE = "service"
    SESSION_TOKEN = "session_token"
    TOKEN = "token"
    TOKEN_AUTH = "token_auth"
    TOKEN_URL = "token_url"
    USERNAME = "username"
    VALUE = "value"
    VERIFY_SSL = "verify_ssl"


class AuthDialog(QDialog):
    auth_configured = pyqtSignal(object)

    _WINDOW_TITLE = "Configure Authentication"
    _MINIMUM_WIDTH = 540
    _MINIMUM_HEIGHT = 480
    _PICKER_MINIMUM_WIDTH = 220
    _PICKER_PLACEHOLDER = "— fill in manually —"
    _DEFAULT_API_KEY_NAME = "X-API-Key"  # pragma: allowlist secret
    _DEFAULT_TOKEN_AUTH = "body"

    def __init__(
        self,
        current_auth: AuthStrategy | None = None,
        parent: QWidget | None = None,
        db: Database | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.current_auth = current_auth
        self._auth_type_to_tab: dict[str, int] = {}
        self._auth_config_appliers: dict[str, Callable[[dict[str, Any]], None]] = {}

        self.setWindowTitle(self._WINDOW_TITLE)
        self.setMinimumSize(self._MINIMUM_WIDTH, self._MINIMUM_HEIGHT)
        self._init_ui()
        self._init_auth_config_appliers()
        self._refresh_client_picker()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        picker_frame = QFrame()
        picker_frame.setFrameShape(QFrame.Shape.StyledPanel)
        picker_layout = QHBoxLayout(picker_frame)
        picker_layout.setContentsMargins(6, 4, 6, 4)

        picker_layout.addWidget(QLabel("Saved credential:"))
        self.cred_picker = QComboBox()
        self.cred_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cred_picker.setMinimumWidth(self._PICKER_MINIMUM_WIDTH)
        self.cred_picker.addItem(self._PICKER_PLACEHOLDER, userData=None)
        self.cred_picker.currentIndexChanged.connect(self._on_client_picked)
        picker_layout.addWidget(self.cred_picker, 1)

        manage_btn = QPushButton("Manage Credentials…")
        manage_btn.setFlat(True)
        manage_btn.clicked.connect(self._open_client_manager)
        picker_layout.addWidget(manage_btn)

        layout.addWidget(picker_frame)

        self.tabs = QTabWidget()

        self.no_auth = QWidget()
        self.basic = BasicAuthTab()
        self.bearer = BearerAuthTab()
        self.oauth2 = OAuth2AuthTab()
        self.api_key = ApiKeyAuthTab()
        self.aws = AwsSigV4AuthTab()

        self._auth_type_to_tab = {
            AuthType.NO_AUTH: self.tabs.addTab(self.no_auth, "No Auth"),
            AuthType.BASIC: self.tabs.addTab(self.basic, "Basic Auth"),
            AuthType.BEARER: self.tabs.addTab(self.bearer, "Bearer Token"),
            AuthType.OAUTH2: self.tabs.addTab(self.oauth2, "OAuth 2.0"),
            AuthType.API_KEY: self.tabs.addTab(self.api_key, "API Key"),
            AuthType.AWS_SIGV4: self.tabs.addTab(self.aws, "AWS SigV4"),
        }

        layout.addWidget(self.tabs)

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

    def _init_auth_config_appliers(self) -> None:
        """Register handlers used to apply saved authentication configuration."""
        self._auth_config_appliers = {
            AuthType.OAUTH2: self._apply_oauth2_config,
            AuthType.API_KEY: self._apply_api_key_config,
            AuthType.BASIC: self._apply_basic_config,
            AuthType.BEARER: self._apply_bearer_config,
            AuthType.AWS_SIGV4: self._apply_aws_sigv4_config,
        }

    def _refresh_client_picker(self) -> None:
        """Reload the saved-credential combo from the database."""
        if not self.db:
            return

        manager = SavedCredentialsManager(self.db)
        credentials = manager.list()
        selected_credential_id = self.cred_picker.currentData()

        self.cred_picker.blockSignals(True)
        try:
            self.cred_picker.clear()
            self.cred_picker.addItem(self._PICKER_PLACEHOLDER, userData=None)

            for credential in credentials:
                credential_id = credential.get(AuthConfigKey.ID)
                auth_type = str(credential.get(AuthConfigKey.AUTH_TYPE, ""))
                type_label = AUTH_TYPES.get(auth_type, auth_type)
                default_prefix = "★ " if credential.get(AuthConfigKey.IS_DEFAULT) else ""
                label = f"{default_prefix}[{type_label}] {credential.get(AuthConfigKey.NAME, '')}"

                self.cred_picker.addItem(label, userData=credential_id)

                if credential_id == selected_credential_id:
                    self.cred_picker.setCurrentIndex(self.cred_picker.count() - 1)
        finally:
            self.cred_picker.blockSignals(False)

        self._select_default_credential(manager)

    def _select_default_credential(self, manager: SavedCredentialsManager) -> None:
        """Select the default saved credential when no current auth is configured."""
        if self.cred_picker.currentIndex() != 0 or self.current_auth:
            return

        for index in range(1, self.cred_picker.count()):
            credential_id = self.cred_picker.itemData(index)
            if credential_id is None:
                continue

            credential = manager.get(credential_id) or {}
            if credential.get(AuthConfigKey.IS_DEFAULT):
                self.cred_picker.setCurrentIndex(index)
                return

    def _on_client_picked(self, index: int) -> None:
        """Auto-fill fields and switch tab when a saved credential is selected."""
        if not self.db:
            return

        credential_id = self.cred_picker.currentData()
        if credential_id is None:
            return

        try:
            saved_credential = SavedCredentialsManager(self.db).get(credential_id)
        except RuntimeError:
            LOGGER.exception("Failed to retrieve saved credential.")
            QMessageBox.warning(
                self,
                "Credential unavailable",
                "The selected credential could not be loaded.",
            )
            return

        if not saved_credential:
            return

        auth_type = str(saved_credential.get(AuthConfigKey.AUTH_TYPE, ""))
        config = saved_credential.get(AuthConfigKey.CONFIG, {})
        if not isinstance(config, dict):
            LOGGER.warning("Saved credential has invalid configuration format.")
            return

        self._select_and_apply_config(auth_type, config)

    def _select_and_apply_config(self, auth_type: str, config: dict[str, Any]) -> None:
        """Switch to the relevant tab and apply saved configuration."""
        tab_index = self._auth_type_to_tab.get(auth_type)
        if tab_index is not None:
            self.tabs.setCurrentIndex(tab_index)

        apply_config = self._auth_config_appliers.get(auth_type)
        if apply_config is None:
            LOGGER.warning("Unsupported saved credential authentication type.")
            return

        apply_config(config)

    def _apply_oauth2_config(self, cfg: dict[str, Any]) -> None:
        """Apply OAuth2 settings from a saved credential."""
        self.oauth2.token_url.setText(str(cfg.get(AuthConfigKey.TOKEN_URL, "")))
        self.oauth2.client_id.setText(str(cfg.get(AuthConfigKey.CLIENT_ID, "")))
        self.oauth2.client_secret.setText(str(cfg.get(AuthConfigKey.CLIENT_SECRET, "")))
        self.oauth2.scope.setText(str(cfg.get(AuthConfigKey.SCOPE, "")))

        verify_ssl = cfg.get(AuthConfigKey.VERIFY_SSL, True)
        self.oauth2.verify_ssl.setChecked(verify_ssl is True)

        token_auth = str(
            cfg.get(AuthConfigKey.TOKEN_AUTH, self._DEFAULT_TOKEN_AUTH) or self._DEFAULT_TOKEN_AUTH,
        )
        token_auth_index = self.oauth2.token_auth.findData(token_auth)
        self.oauth2.token_auth.setCurrentIndex(max(token_auth_index, 0))

        self.oauth2.access_token.clear()
        self.oauth2.refresh_token.clear()

    def _apply_api_key_config(self, cfg: dict[str, Any]) -> None:
        """Apply API key settings from a saved credential."""
        key_name = str(cfg.get(AuthConfigKey.KEY, self._DEFAULT_API_KEY_NAME))
        key_value = str(cfg.get(AuthConfigKey.VALUE, ""))
        location = str(cfg.get(AuthConfigKey.LOCATION, ApiKeyLocation.HEADER)).lower()

        self.api_key.key_name.setText(key_name)
        self.api_key.key_value.setText(key_value)

        location_index_by_value = {
            ApiKeyLocation.HEADER: 0,
            ApiKeyLocation.QUERY: 1,
        }
        self.api_key.location.setCurrentIndex(
            location_index_by_value.get(location, location_index_by_value[ApiKeyLocation.HEADER]),
        )

    def _apply_basic_config(self, cfg: dict[str, Any]) -> None:
        """Apply Basic Auth settings from a saved credential."""
        self.basic.username.setText(str(cfg.get(AuthConfigKey.USERNAME, "")))
        self.basic.password.setText(str(cfg.get(AuthConfigKey.PASSWORD, "")))

    def _apply_bearer_config(self, cfg: dict[str, Any]) -> None:
        """Apply Bearer Token settings from a saved credential."""
        self.bearer.token.setText(str(cfg.get(AuthConfigKey.TOKEN, "")))

    def _apply_aws_sigv4_config(self, cfg: dict[str, Any]) -> None:
        """Apply AWS SigV4 settings from a saved credential."""
        self.aws.access_key.setText(str(cfg.get(AuthConfigKey.ACCESS_KEY, "")))
        self.aws.secret_key.setText(str(cfg.get(AuthConfigKey.SECRET_KEY, "")))
        self.aws.region.setText(str(cfg.get(AuthConfigKey.REGION, "")))
        self.aws.service.setText(str(cfg.get(AuthConfigKey.SERVICE, "")))
        self.aws.session_token.setText(str(cfg.get(AuthConfigKey.SESSION_TOKEN, "")))

    def _open_client_manager(self) -> None:
        """Open the saved-credentials manager dialog."""
        if not self.db:
            QMessageBox.information(
                self,
                "Not available",
                "The credential manager is not available in this context.",
            )
            return

        try:
            from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog
        except ImportError:
            LOGGER.exception("Failed to import saved credentials dialog.")
            QMessageBox.warning(
                self,
                "Credential manager unavailable",
                "The credential manager could not be opened.",
            )
            return

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
