"""Auth-config collection for the saved credentials dialog.

Extracted from ``saved_credentials_dialog.py`` to keep that module under
the project's module-size limit — ``AuthConfigCollector`` is fully
self-contained (only reads widget attributes off the dialog it's given),
so it splits out cleanly with no circular dependency back on the dialog
module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from equinox.gui.dialogs._oauth_form_utils import parse_json_object_field


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
