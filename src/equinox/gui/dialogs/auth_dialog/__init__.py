import logging
from collections.abc import Callable
from typing import Any, Literal

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

from equinox.auth import (
    AUTH_TYPES,
    APIKeyAuth,
    AuthStrategy,
    AWSSigV4Auth,
    BasicAuth,
    BearerAuth,
    OAuth2Auth,
)
from equinox.core.exceptions import AuthError
from equinox.storage import Database, SavedCredentialsManager

from .oauth2.controller import OAuth2TokenController
from .tabs.base import AuthDialogTab
from .tabs.api_key import ApiKeyAuthTab
from .tabs.aws import AwsSigV4AuthTab
from .tabs.basic import BasicAuthTab
from .tabs.bearer import BearerAuthTab
from .tabs.oauth2 import OAuth2AuthTab

LOGGER = logging.getLogger(__name__)


def _sanitize_field(text: str) -> str:
    """Strip CR/LF characters that password managers may paste into fields.

    Prevents ``AuthError`` (CRLF-injection check) from firing on values
    that are merely copy-paste artefacts rather than real attacks.
    """
    return text.replace("\r", "").replace("\n", "")


# Sentinel returned by the tab builders when a required-field check fails and
# a QMessageBox has already been shown.  Distinct from ``None``, which is the
# legitimate "No Auth" result.
_MISSING: Any = object()


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
        self._saved_auth: AuthStrategy | None = None
        self._auth_type_to_tab: dict[str, int] = {}
        self._auth_config_appliers: dict[str, Callable[[dict[str, Any]], None]] = {}

        self.setWindowTitle(self._WINDOW_TITLE)
        self.setMinimumSize(self._MINIMUM_WIDTH, self._MINIMUM_HEIGHT)
        self._init_ui()
        self._init_auth_config_appliers()
        self._load_current_auth()
        self._refresh_client_picker()

    @property
    def _last_fetched_auth(self) -> OAuth2Auth | None:
        """The OAuth2 strategy from the most recent successful "Fetch Token…"."""
        return self.oauth2_controller.last_fetched_auth

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

    def _load_current_auth(self) -> None:
        """Populate the tabs from the auth strategy the caller passed in.

        Without this, opening the dialog on an already-configured request shows
        blank fields and saving silently discards the existing credentials.
        """
        auth = self.current_auth
        if auth is None:
            self._select_tab(AuthType.NO_AUTH)
            return

        loaders: dict[type, Callable[[Any], None]] = {
            BasicAuth: self._load_basic_auth,
            BearerAuth: self._load_bearer_auth,
            OAuth2Auth: self._load_oauth2_auth,
            APIKeyAuth: self._load_api_key_auth,
            AWSSigV4Auth: self._load_aws_sigv4_auth,
        }
        for auth_class, load in loaders.items():
            if isinstance(auth, auth_class):
                load(auth)
                return

        LOGGER.warning("Unsupported auth strategy type: %s", type(auth).__name__)
        self._select_tab(AuthType.NO_AUTH)

    def _select_tab(self, auth_type: str) -> None:
        """Switch to the tab registered for ``auth_type``."""
        tab_index = self._auth_type_to_tab.get(auth_type)
        if tab_index is not None:
            self.tabs.setCurrentIndex(tab_index)

    @staticmethod
    def _auth_text(auth: Any, attribute: str) -> str:
        """Read an optional string attribute off an auth strategy."""
        return str(getattr(auth, attribute, "") or "")

    def _load_basic_auth(self, auth: BasicAuth) -> None:
        self._select_tab(AuthType.BASIC)
        self.basic.username.setText(self._auth_text(auth, AuthConfigKey.USERNAME))
        self.basic.password.setText(self._auth_text(auth, AuthConfigKey.PASSWORD))

    def _load_bearer_auth(self, auth: BearerAuth) -> None:
        self._select_tab(AuthType.BEARER)
        self.bearer.token.setText(self._auth_text(auth, AuthConfigKey.TOKEN))

    def _load_oauth2_auth(self, auth: OAuth2Auth) -> None:
        self._select_tab(AuthType.OAUTH2)
        self.oauth2.token_url.setText(self._auth_text(auth, AuthConfigKey.TOKEN_URL))
        self.oauth2.client_id.setText(self._auth_text(auth, AuthConfigKey.CLIENT_ID))
        self.oauth2.client_secret.setText(self._auth_text(auth, AuthConfigKey.CLIENT_SECRET))
        self.oauth2.scope.setText(self._auth_text(auth, AuthConfigKey.SCOPE))
        self.oauth2.access_token.setText(self._auth_text(auth, "access_token"))
        self.oauth2.refresh_token.setText(self._auth_text(auth, "refresh_token"))
        self.oauth2.verify_ssl.setChecked(
            getattr(auth, AuthConfigKey.VERIFY_SSL, True) is not False,
        )

        token_auth = self._auth_text(auth, AuthConfigKey.TOKEN_AUTH) or self._DEFAULT_TOKEN_AUTH
        token_auth_index = self.oauth2.token_auth.findData(token_auth)
        self.oauth2.token_auth.setCurrentIndex(max(token_auth_index, 0))

    def _load_api_key_auth(self, auth: APIKeyAuth) -> None:
        self._select_tab(AuthType.API_KEY)
        self.api_key.key_name.setText(self._auth_text(auth, AuthConfigKey.KEY))
        self.api_key.key_value.setText(self._auth_text(auth, AuthConfigKey.VALUE))
        location = self._auth_text(auth, AuthConfigKey.LOCATION).lower()
        self.api_key.location.setCurrentIndex(1 if location == ApiKeyLocation.QUERY else 0)

    def _load_aws_sigv4_auth(self, auth: AWSSigV4Auth) -> None:
        self._select_tab(AuthType.AWS_SIGV4)
        self.aws.access_key.setText(self._auth_text(auth, AuthConfigKey.ACCESS_KEY))
        self.aws.secret_key.setText(self._auth_text(auth, AuthConfigKey.SECRET_KEY))
        self.aws.region.setText(self._auth_text(auth, AuthConfigKey.REGION))
        self.aws.service.setText(self._auth_text(auth, AuthConfigKey.SERVICE))
        self.aws.session_token.setText(self._auth_text(auth, AuthConfigKey.SESSION_TOKEN))

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
        self._select_tab(auth_type)

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
        # The manager emits this on every mutation; an unconditional refresh
        # afterwards could re-select the default and overwrite typed-in fields.
        dlg.credentials_changed.connect(self._refresh_client_picker)
        dlg.exec()

    # ── Saving ────────────────────────────────────────────────────────

    def _save_auth(self) -> None:
        """Build an auth strategy from the active tab and hand it to the caller.

        The result is published two ways: on ``_saved_auth`` for callers that
        read it after ``exec()``, and via the ``auth_configured`` signal.
        """
        try:
            auth = self._build_auth_from_tab()
        except AuthError as exc:
            QMessageBox.warning(
                self,
                "Invalid Credentials",
                f"Could not save authentication:\n{exc}",
            )
            return

        if auth is _MISSING:
            # A required-field check failed; the warning has already been shown.
            return

        self._saved_auth = auth
        self.accept()
        self.auth_configured.emit(auth)

    def _build_auth_from_tab(self) -> AuthStrategy | None | Any:
        """Construct an auth strategy from the current tab's fields.

        Returns:
            An auth strategy, ``None`` for "No Auth", or :data:`_MISSING` when a
            required field is empty (a warning has already been shown).

        Raises:
            AuthError: When credential validation fails (CRLF, length, etc.).
        """
        builders: dict[str, Callable[[dict[str, Any]], AuthStrategy | None | Any]] = {
            AuthType.BASIC: self._build_basic_auth,
            AuthType.BEARER: self._build_bearer_auth,
            AuthType.OAUTH2: self._build_oauth2_auth,
            AuthType.API_KEY: self._build_api_key_auth,
            AuthType.AWS_SIGV4: self._build_aws_sigv4_auth,
        }

        tab = self.tabs.currentWidget()
        auth_type = self._current_auth_type()
        build = builders.get(auth_type or "")
        if build is None or not isinstance(tab, AuthDialogTab):
            return None
        return build(self._sanitized_config(dict(tab.get_auth_config())))

    def _current_auth_type(self) -> str | None:
        """Return the auth-type identifier for the currently selected tab."""
        current_index = self.tabs.currentIndex()
        for auth_type, tab_index in self._auth_type_to_tab.items():
            if tab_index == current_index:
                return auth_type
        return None

    @staticmethod
    def _sanitized_config(config: dict[str, Any]) -> dict[str, Any]:
        """Strip stray CR/LF from every string value in a tab's config."""
        return {
            key: _sanitize_field(value).strip() if isinstance(value, str) else value
            for key, value in config.items()
        }

    def _warn_missing(self, message: str) -> Any:
        QMessageBox.warning(self, "Missing Fields", message)
        return _MISSING

    def _build_basic_auth(self, cfg: dict[str, Any]) -> AuthStrategy | Any:
        username = cfg.get(AuthConfigKey.USERNAME) or ""
        password = cfg.get(AuthConfigKey.PASSWORD) or ""
        if not username or not password:
            return self._warn_missing("Enter both username and password.")
        return BasicAuth(username=username, password=password)

    def _build_bearer_auth(self, cfg: dict[str, Any]) -> AuthStrategy | Any:
        token = cfg.get(AuthConfigKey.TOKEN) or ""
        if not token:
            return self._warn_missing("Enter a bearer token.")
        return BearerAuth(token=token)

    def _build_oauth2_auth(self, cfg: dict[str, Any]) -> AuthStrategy | Any:
        token_url = cfg.get(AuthConfigKey.TOKEN_URL) or ""
        client_id = cfg.get(AuthConfigKey.CLIENT_ID) or ""
        if not token_url or not client_id:
            return self._warn_missing("Token URL and Client ID are required.")

        token_auth: Literal["body", "basic"] = (
            "basic" if cfg.get(AuthConfigKey.TOKEN_AUTH) == "basic" else "body"
        )
        auth = OAuth2Auth(
            token_url=token_url,
            client_id=client_id,
            client_secret=cfg.get(AuthConfigKey.CLIENT_SECRET) or None,
            scope=cfg.get(AuthConfigKey.SCOPE) or None,
            access_token=cfg.get("access_token") or None,
            refresh_token=cfg.get("refresh_token") or None,
            verify_ssl=bool(cfg.get(AuthConfigKey.VERIFY_SSL, True)),
            token_auth=token_auth,
        )
        self._carry_forward_expiry(auth)
        return auth

    def _carry_forward_expiry(self, auth: OAuth2Auth) -> None:
        """Copy ``expires_at`` from a just-fetched token so it isn't eternal.

        The token fields only carry the token string, so a token obtained via
        "Fetch Token…" would otherwise be saved without its expiry and never
        refreshed.
        """
        fetched = self._last_fetched_auth
        if fetched is None or fetched.expires_at is None:
            return
        if auth.access_token and auth.access_token == fetched.access_token:
            auth.expires_at = fetched.expires_at

    def _build_api_key_auth(self, cfg: dict[str, Any]) -> AuthStrategy | Any:
        key = cfg.get(AuthConfigKey.KEY) or ""
        value = cfg.get(AuthConfigKey.VALUE) or ""
        if not key or not value:
            return self._warn_missing("Enter both key name and value.")
        location: Literal["header", "query"] = (
            "query"
            if str(cfg.get(AuthConfigKey.LOCATION, "")).lower() == ApiKeyLocation.QUERY
            else "header"
        )
        return APIKeyAuth(key=key, value=value, location=location)

    def _build_aws_sigv4_auth(self, cfg: dict[str, Any]) -> AuthStrategy | Any:
        access_key = cfg.get(AuthConfigKey.ACCESS_KEY) or ""
        secret_key = cfg.get(AuthConfigKey.SECRET_KEY) or ""
        region = cfg.get(AuthConfigKey.REGION) or ""
        service = cfg.get(AuthConfigKey.SERVICE) or ""
        if not access_key or not secret_key or not region or not service:
            return self._warn_missing(
                "Access Key, Secret Key, Region and Service are required.",
            )
        return AWSSigV4Auth(
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            service=service,
            session_token=cfg.get(AuthConfigKey.SESSION_TOKEN) or None,
        )
