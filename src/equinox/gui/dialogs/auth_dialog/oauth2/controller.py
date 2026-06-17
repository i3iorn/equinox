from __future__ import annotations

from collections.abc import Callable
from typing import Any
from typing import Literal

from equinox.auth import OAuth2Auth
from equinox.core.exceptions import AuthError
from equinox.core.interpolation import collect_interpolation_variables
from equinox.core.interpolation import VariableInterpolator
from equinox.storage import Database
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QWidget

from ..tabs.oauth2 import OAuth2AuthTab
from .response_dialog import OAuth2TokenResponseDialog
from .worker import OAuth2TokenFetchWorker


TokenAuthMode = Literal["basic", "body"]


class OAuth2TokenController(QObject):
    """Controller responsible for securely fetching OAuth2 tokens."""

    _ALLOWED_TOKEN_AUTH: set[TokenAuthMode] = {"basic", "body"}

    def __init__(
        self,
        tab: OAuth2AuthTab,
        db: Database | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tab = tab
        self._db = db
        self._last_response: dict[str, Any] | None = None
        self._worker: OAuth2TokenFetchWorker | None = None

        tab.fetch_btn.clicked.connect(self.fetch_token)
        tab.view_btn.clicked.connect(self.show_response)

    def fetch_token(self) -> None:
        """Validate inputs, build OAuth2Auth, and start background fetch."""
        config: dict[str, Any] = self._tab.get_auth_config()

        try:
            token_url_raw = config.get("token_url")
            client_id_raw = config.get("client_id")
            token_auth_raw = config.get("token_auth")

            token_url = self._require_nonempty(self._as_optional_str(token_url_raw), "Token URL")
            client_id = self._require_nonempty(self._as_optional_str(client_id_raw), "Client ID")
            token_auth = self._validate_token_auth(token_auth_raw)
        except AuthError as exc:
            self._tab.status.setText(str(exc))
            return

        variables = self._load_interpolation_variables()
        interpolate = self._build_interpolator(variables)

        client_secret = self._optional_interpolated(
            interpolate,
            self._as_optional_str(config.get("client_secret")),
        )
        scope = self._optional_interpolated(
            interpolate,
            self._as_optional_str(config.get("scope")),
        )

        auth = OAuth2Auth(
            token_url=interpolate(token_url),
            client_id=interpolate(client_id),
            client_secret=client_secret,
            scope=scope,
            verify_ssl=bool(config.get("verify_ssl", True)),
            token_auth=token_auth,
        )

        self._prepare_ui_for_fetch()
        self._start_worker(auth)

    def show_response(self) -> None:
        """Display the last token endpoint response."""
        if not self._last_response:
            return
        dialog = OAuth2TokenResponseDialog(self._last_response, self._tab)
        dialog.exec()

    def _start_worker(self, auth: OAuth2Auth) -> None:
        worker = OAuth2TokenFetchWorker(auth, self)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _prepare_ui_for_fetch(self) -> None:
        self._tab.fetch_btn.setEnabled(False)
        self._tab.view_btn.setEnabled(False)
        self._tab.status.setText("Fetching…")

    def _on_worker_finished(self, result: dict[str, Any]) -> None:
        self._tab.fetch_btn.setEnabled(True)
        self._last_response = result.get("response")
        self._tab.view_btn.setEnabled(self._last_response is not None)

        if not result.get("ok"):
            error_msg = str(result.get("error", "Unknown error"))
            self._tab.status.setText(f"Error: {error_msg}")
            return

        auth = result.get("auth")
        if not isinstance(auth, OAuth2Auth):
            self._tab.status.setText("Error: Invalid auth response")
            return

        self._tab.access_token.setText(auth.access_token or "")
        if auth.refresh_token:
            self._tab.refresh_token.setText(auth.refresh_token)

        self._tab.status.setText("Token acquired")

    @staticmethod
    def _as_optional_str(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def _require_nonempty(value: str | None, field: str) -> str:
        if value is None or not value.strip():
            raise AuthError(f"{field} is required.")
        return value.strip()

    def _validate_token_auth(self, value: Any) -> TokenAuthMode:
        value_str = self._as_optional_str(value)
        if value_str not in self._ALLOWED_TOKEN_AUTH:
            raise AuthError("Invalid token_auth: must be 'basic' or 'body'.")
        return value_str  # type: ignore[return-value]

    def _load_interpolation_variables(self) -> dict[str, str]:
        if not self._db:
            return {}
        try:
            return dict(collect_interpolation_variables(self._db))
        except Exception:
            return {}

    @staticmethod
    def _build_interpolator(variables: dict[str, str]) -> Callable[[str], str]:
        if not variables:
            return lambda v: v

        def interpolate(value: str) -> str:
            return str(VariableInterpolator.interpolate(value, variables))

        return interpolate

    @staticmethod
    def _optional_interpolated(
        interpolate: Callable[[str], str],
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return interpolate(value)
