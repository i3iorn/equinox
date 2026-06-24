"""Auth inheritance, comparison, and display helpers for ``RequestPanel``."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from equinox.auth import OAuth2Auth
from equinox.core.request import Request
from equinox.core.util.time import utc_now
from equinox.gui.request_panel._constants import AUTH_VOLATILE_KEYS
from equinox.gui.request_panel._constants import FOLDER_AUTH_PREFIX
from equinox.security import mask_secret

logger = logging.getLogger(__name__)

_AUTH_NONE_LABEL = "Auth: None"
_AUTH_NONE_DESC = "No authentication configured"
_AUTH_TOKEN_NONE = "Token: None"
_AUTH_TOKEN_EXPIRING = "Token: Expiring soon"
_AUTH_TOKEN_VALID = "Token: Valid"
_AUTH_INHERITED_FROM_COLLECTION = "  (inherited from collection)"
_AUTH_INHERITED_FROM_FOLDER = '  (inherited from folder "{}")'
_TRUST_PREFIX = "Trust: "


class AuthDisplayMixin:
    """Manage auth inheritance resolution and auth-tab display rendering."""

    _request_persistence: Any
    _auth: Any | None
    _inherited_auth: Any | None
    _inherited_auth_source: str | None
    auth_status_label: Any
    auth_type_label: Any
    auth_details_label: Any
    auth_trust_label: Any
    url_input: Any
    verify_ssl_check: Any

    @staticmethod
    def _auth_configs_match(left: Any, right: Any) -> bool:
        """Return ``True`` when two auth objects share the same stable config."""
        if left is None and right is None:
            return True
        if left is None or right is None:
            return False
        try:
            left_data = left.to_dict()
            right_data = right.to_dict()
        except Exception:
            logger.warning("Auth config comparison failed", exc_info=True)
            return False
        if not isinstance(left_data, dict) or not isinstance(right_data, dict):
            return False
        for key in AUTH_VOLATILE_KEYS:
            left_data.pop(key, None)
            right_data.pop(key, None)
        return left_data == right_data

    def _build_auth_probe(self) -> Request | None:
        """Build a minimal request used for inherited-auth resolution."""
        request = getattr(self, "current_request", None)
        collection_id = getattr(request, "collection_id", None)
        folder = getattr(request, "folder", None)
        if collection_id is None and hasattr(self, "_build_request_editor_snapshot"):
            try:
                snapshot = self._build_request_editor_snapshot()
                collection_id = getattr(snapshot, "collection_id", None)
                folder = getattr(snapshot, "folder", folder)
            except Exception:
                logger.exception("Failed to build auth probe from editor snapshot", exc_info=True)
        if not collection_id:
            return None
        method = getattr(request, "method", "GET") if request is not None else "GET"
        url = getattr(request, "url", "") if request is not None else ""
        return Request(method=method, url=url, collection_id=collection_id, folder=folder, auth=None)

    def _resolve_inherited_auth(self) -> None:
        """Refresh inherited auth from collection and folder sources."""
        self._inherited_auth = None
        self._inherited_auth_source = None
        probe = self._build_auth_probe()
        if probe is None:
            return
        try:
            inherited_auth, inherited_source = self._request_persistence.resolve_effective_auth(probe)
        except Exception as exc:
            logger.warning("Failed to resolve inherited auth: %s", exc)
            return
        if inherited_auth is None:
            return
        self._inherited_auth = inherited_auth
        self._inherited_auth_source = inherited_source

    def refresh_inherited_auth(self) -> None:
        """Public hook for external callers to refresh inherited auth state."""
        if self._auth is not None:
            return
        self._resolve_inherited_auth()
        self._update_auth_display(self._auth)

    @staticmethod
    def _format_inherited_label(source: str | None) -> str:
        """Return a human-readable inherited-auth suffix."""
        if not source:
            return ""
        if source.startswith(FOLDER_AUTH_PREFIX):
            folder = source[len(FOLDER_AUTH_PREFIX) :]
            return _AUTH_INHERITED_FROM_FOLDER.format(folder)
        if source == "collection":
            return _AUTH_INHERITED_FROM_COLLECTION
        return ""

    def _update_auth_display(self, auth: Any = None) -> None:
        """Refresh the auth tab labels for the effective auth state."""
        self.auth_status_label.setText("")
        display_auth = auth or getattr(self, "_inherited_auth", None)
        if display_auth is None:
            self._set_auth_display_none()
            self._update_trust_indicator(None)
            return
        inherited_label = ""
        if auth is None and self._inherited_auth_source:
            inherited_label = self._format_inherited_label(self._inherited_auth_source)
        self._set_auth_display_for_strategy(display_auth, inherited_label)
        self._update_trust_indicator(display_auth)

    def _set_auth_display_none(self) -> None:
        """Render the auth tab in the no-auth state."""
        self.auth_type_label.setText(_AUTH_NONE_LABEL)
        self.auth_details_label.setText(_AUTH_NONE_DESC)

    def _update_trust_indicator(self, auth: Any | None) -> None:
        """Render a compact auth/environment/transport posture summary."""
        chips = self._build_trust_indicator_chips(auth)
        self.auth_trust_label.setText(_TRUST_PREFIX + " | ".join(chips))

    def _build_trust_indicator_chips(self, auth: Any | None) -> list[str]:
        """Build the trust-indicator chips shown in the auth tab."""
        chips = [self._auth_trust_chip(auth), self._url_trust_chip(), self._ssl_trust_chip()]
        return chips

    def _auth_trust_chip(self, auth: Any | None) -> str:
        """Describe where the effective auth comes from."""
        if auth is None:
            return "NoAuth"
        if self._auth is not None:
            return "OwnAuth"
        if self._inherited_auth_source:
            return f"Inherited:{self._inherited_auth_source}"
        return "Inherited"

    def _url_trust_chip(self) -> str:
        """Describe whether the URL depends on environment interpolation."""
        try:
            return "EnvVars" if "{{" in self.url_input.text().strip() else "DirectURL"
        except Exception:
            return "URL:unknown"

    def _ssl_trust_chip(self) -> str:
        """Describe the current SSL verification posture."""
        try:
            return "SSL:verified" if self.verify_ssl_check.isChecked() else "SSL:unverified"
        except Exception:
            return "SSL:unknown"

    def _set_auth_display_for_strategy(self, auth: Any, inherited_label: str) -> None:
        """Render auth labels for a specific strategy object."""
        type_name = getattr(auth, "DISPLAY_NAME", None) or type(auth).__name__
        self.auth_type_label.setText(f"Auth: {type_name}{inherited_label}")
        self.auth_details_label.setText(self._get_auth_display_summary(auth))
        if isinstance(auth, OAuth2Auth):
            self._render_oauth2_token_status(auth)

    @staticmethod
    def _get_auth_display_summary(auth: Any) -> str:
        """Return the auth strategy display summary when available."""
        if not hasattr(auth, "get_display_summary"):
            return ""
        try:
            return str(auth.get_display_summary())
        except Exception as exc:
            logger.debug("Failed to get auth display summary: %s", exc, exc_info=True)
            return ""

    def _render_oauth2_token_status(self, auth: OAuth2Auth) -> None:
        """Render the OAuth2 token status line."""
        try:
            info = auth.get_token_info()
            text = self._build_oauth2_status_text(auth, info)
            self.auth_status_label.setText(self._append_oauth2_expiry_countdown(text, info))
        except Exception as exc:
            logger.debug("Failed to render OAuth2 token status: %s", exc, exc_info=True)

    @staticmethod
    def _build_oauth2_status_text(auth: OAuth2Auth, info: dict[str, Any]) -> str:
        """Build the base OAuth2 status text without countdown details."""
        if not auth.access_token:
            return _AUTH_TOKEN_NONE
        token_display = mask_secret(info.get("access_token", ""))
        if info.get("needs_refresh"):
            return f"{_AUTH_TOKEN_EXPIRING}  [{token_display}]"
        return f"{_AUTH_TOKEN_VALID}  [{token_display}]"

    @staticmethod
    def _append_oauth2_expiry_countdown(text: str, info: dict[str, Any]) -> str:
        """Append expiry countdown details to OAuth2 status text when available."""
        expires_at = info.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at:
            return text
        try:
            expiry_dt = datetime.fromisoformat(expires_at)
        except (TypeError, ValueError) as exc:
            logger.debug("Failed to parse OAuth2 token expiry: %s", exc)
            return text
        seconds = int((expiry_dt - utc_now()).total_seconds())
        if seconds > 0:
            return f"{text}  (expires in {seconds}s)"
        return f"{text}  (expired)"
