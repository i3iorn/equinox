"""Auth configuration and display mixin for RequestPanel.

Contains ``_RequestAuthMixin`` — all methods related to authentication
configuration, display rendering, inheritance resolution, and token
persistence back to collection/folder sources.

This mixin has no ``__init__`` and relies on ``self.*`` attributes set by
``RequestPanel.__init__`` (PyQt6 MRO is respected).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QDialog,
)

from equinox.auth import BearerAuth, BasicAuth, APIKeyAuth, OAuth2Auth
from equinox.core.redact import mask_secret
from equinox.core.time import utc_now
from equinox.gui.theme import Colors

from equinox.gui.request_panel._constants import (
    APIKEY_PREVIEW_LENGTH,
    AUTH_DISPLAY_DISPATCH,
    AUTH_TAB_MARGINS,
    AUTH_VOLATILE_KEYS,
    FOLDER_AUTH_PREFIX,
)
from equinox.gui.request_panel.mixins._helpers import write_auth_to_source

logger = logging.getLogger(__name__)


class _RequestAuthMixin:
    """Methods for authentication configuration, display, and persistence.

    Responsible for:
    - Auth tab UI creation
    - Auth configuration dialog interaction
    - Auth display across all supported auth types
    - Auth inheritance resolution (from folder/collection)
    - Auth comparison (excluding volatile token fields)
    - Token persistence to source (collection/folder)
    """

    # ── Token persistence ─────────────────────────────────────────────

    def _save_inherited_token_to_source(self, auth: Any) -> None:
        """Write freshly-fetched token back to the collection/folder it came from.

        Used when the user fetches a token via the auth dialog while the
        request is still using inherited auth. The token belongs to the
        collection/folder, not to the request.

        Args:
            auth: Auth strategy with updated token
        """
        source = getattr(self, "_inherited_auth_source", None)
        if not source:
            return
        req = self.current_request
        if not req or not req.collection_id:
            return
        try:
            write_auth_to_source(self._collection_mgr, req.collection_id, source, auth)
            logger.debug("Saved dialog-fetched token to %s", source)
        except Exception as exc:
            logger.debug("Failed to save dialog token to source: %s", exc)

    # ── Tab creation ──────────────────────────────────────────────────

    def _create_auth_tab(self) -> QWidget:
        """Create the authentication configuration tab.

        Returns:
            QWidget with auth labels and configure/clear buttons
        """
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(*AUTH_TAB_MARGINS)
        self.auth_type_label = QLabel("Auth: None")
        self.auth_type_label.setStyleSheet("font-weight: bold;")
        self.auth_details_label = QLabel("No authentication configured")
        self.auth_details_label.setObjectName("mutedLabel")
        self.auth_details_label.setWordWrap(True)
        self.auth_status_label = QLabel("")
        self.auth_status_label.setWordWrap(True)
        configure_btn = QPushButton("Configure Authentication…")
        configure_btn.clicked.connect(self._configure_auth)
        clear_btn = QPushButton("Clear Auth")
        clear_btn.clicked.connect(self._clear_auth)
        btn_row = QHBoxLayout()
        btn_row.addWidget(configure_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        layout.addWidget(self.auth_type_label)
        layout.addWidget(self.auth_details_label)
        layout.addWidget(self.auth_status_label)
        layout.addLayout(btn_row)
        layout.addStretch()
        return w

    # ── Configuration ─────────────────────────────────────────────────

    def _configure_auth(self) -> None:
        from equinox.gui.dialogs.auth_dialog import AuthDialog
        # Show inherited auth in the dialog so the user sees what's active
        was_inherited = self._auth is None and self._inherited_auth is not None
        display_auth = self._auth or self._inherited_auth
        dialog = AuthDialog(display_auth, self, db=self.db)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not hasattr(dialog, '_saved_auth'):
            return

        saved = dialog._saved_auth
        fetched_token = getattr(dialog, '_last_fetched_auth', None)

        # ── Guard: don't accidentally bake inherited auth into the request ──
        #
        # If the request was using inherited auth (from collection/folder)
        # and the user opened the dialog without changing the underlying
        # configuration, we must NOT set self._auth — that would store a
        # copy of the collection's auth on the request row.
        if was_inherited and saved is not None:
            configs_match = self._auth_configs_match(saved, self._inherited_auth)

            # Case A — unchanged config, no token fetch: no-op.
            if configs_match and not fetched_token:
                return

            # Case B — unchanged config, token fetched: persist at source.
            if configs_match and fetched_token is not None:
                self._inherited_auth = saved
                self._save_inherited_token_to_source(saved)
                self._update_auth_display(self._auth)  # self._auth still None
                return

        # Case C — user explicitly set a different auth (or changed the
        # config): honour it as own auth on the request.
        old_auth = self._auth
        self._auth = saved
        if self._auth is not None:
            # Own auth supersedes inherited
            self._inherited_auth = None
            self._inherited_auth_source = None
        else:
            # User chose "No Auth" — re-resolve inherited
            self._resolve_inherited_auth()
        self._update_auth_display(self._auth)
        # Mark dirty if auth actually changed
        if not self._auth_configs_match(old_auth, self._auth):
            self._mark_dirty()

    def _clear_auth(self) -> None:
        had_auth = self._auth is not None
        self._auth = None
        self._resolve_inherited_auth()
        self._update_auth_display(None)
        if had_auth:
            self._mark_dirty()

    # ── Comparison ────────────────────────────────────────────────────

    @staticmethod
    def _auth_configs_match(a: Any, b: Any) -> bool:
        """Return True if two auth objects have the same configuration.

        Excludes volatile / token-state fields that change without user action
        so that, e.g., a token refresh does not make the dialog think the user
        changed the inherited auth configuration.
        """
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        try:
            d1 = a.to_dict()
            d2 = b.to_dict()
            for key in AUTH_VOLATILE_KEYS:
                d1.pop(key, None)
                d2.pop(key, None)
            return d1 == d2
        except Exception:
            logger.warning("Auth config comparison failed", exc_info=True)
            return False

    # ── Inheritance resolution ────────────────────────────────────────

    def _resolve_inherited_auth(self) -> None:
        """Re-resolve inherited auth from the collection/folder hierarchy.

        Called after clearing own auth, after the auth dialog sets "No Auth",
        and when the collection's auth configuration changes externally.
        """
        self._inherited_auth = None
        self._inherited_auth_source = None
        probe = self._build_auth_probe()
        if probe is None:
            return
        try:
            inh_auth, inh_source = self._collection_mgr.resolve_effective_auth(probe)
            if inh_auth is not None:
                self._inherited_auth = inh_auth
                self._inherited_auth_source = inh_source
        except Exception as exc:
            logger.debug("Failed to resolve inherited auth: %s", exc)

    def refresh_inherited_auth(self) -> None:
        """Public method for external callers (e.g. window signal wiring)
        to trigger an inherited-auth refresh and update the display."""
        if self._auth is None:
            self._resolve_inherited_auth()
            self._update_auth_display(self._auth)

    # ── Display ───────────────────────────────────────────────────────

    @staticmethod
    def _format_inherited_label(source: Optional[str]) -> str:
        """Build a human-readable '(inherited from …)' suffix."""
        if not source:
            return ""
        if source.startswith(FOLDER_AUTH_PREFIX):
            folder = source[len(FOLDER_AUTH_PREFIX):]
            return f'  (inherited from folder "{folder}")'
        if source == "collection":
            return "  (inherited from collection)"
        return ""

    def _update_auth_display(self, auth: Any = None) -> None:
        """Update the auth tab labels to reflect current auth state.

        Shows own auth if set, otherwise inherited auth. Falls back to
        "No authentication configured" when neither is present.

        Args:
            auth: Own auth strategy or None
        """
        self.auth_status_label.setText("")
        self.auth_status_label.setStyleSheet("")

        # If no own auth, check inherited
        display_auth = auth
        inherited_label = ""
        if not display_auth and getattr(self, "_inherited_auth", None):
            display_auth = self._inherited_auth
            inherited_label = self._format_inherited_label(
                getattr(self, "_inherited_auth_source", None),
            )

        if not display_auth:
            self.auth_type_label.setText("Auth: None")
            self.auth_details_label.setText("No authentication configured")
            return

        for auth_type, method_name in AUTH_DISPLAY_DISPATCH:
            if isinstance(display_auth, auth_type):
                getattr(self, method_name)(display_auth, inherited_label)
                return

        # Unknown auth type (e.g. AWS SigV4)
        type_name = type(display_auth).__name__
        self.auth_type_label.setText(f"Auth: {type_name}{inherited_label}")
        self.auth_details_label.setText("")

    def _display_basic_auth(self, auth: BasicAuth, inherited_label: str) -> None:
        """Populate the auth display labels for Basic authentication."""
        self.auth_type_label.setText(f"Auth: Basic{inherited_label}")
        self.auth_details_label.setText(f"Username: {auth.username}")

    def _display_bearer_auth(self, auth: BearerAuth, inherited_label: str) -> None:
        """Populate the auth display labels for Bearer token authentication."""
        preview = mask_secret(auth.token)
        self.auth_type_label.setText(f"Auth: Bearer Token{inherited_label}")
        self.auth_details_label.setText(f"Token: {preview}")

    def _display_apikey_auth(self, auth: APIKeyAuth, inherited_label: str) -> None:
        """Populate the auth display labels for API Key authentication."""
        preview = (
            auth.value[:APIKEY_PREVIEW_LENGTH] + "…"
            if len(auth.value) > APIKEY_PREVIEW_LENGTH
            else "***"
        )
        self.auth_type_label.setText(f"Auth: API Key{inherited_label}")
        self.auth_details_label.setText(
            f"{auth.key} = {preview}  ({auth.location})"
        )

    def _display_oauth2_auth(self, auth: OAuth2Auth, inherited_label: str) -> None:
        """Populate the auth display labels for an OAuth 2.0 configuration."""
        self.auth_type_label.setText(f"Auth: OAuth 2.0{inherited_label}")
        self.auth_details_label.setText(
            f"Token URL: {auth.token_url or '—'}\nClient ID: {auth.client_id or '—'}"
        )
        info = auth.get_token_info()
        if not auth.access_token:
            text, color = "Token: None", Colors.RED
        elif info["needs_refresh"]:
            text, color = f"Token: Expiring soon  [{info['access_token']}]", Colors.AMBER
        else:
            text, color = f"Token: Valid  [{info['access_token']}]", Colors.GREEN
        if info["expires_at"]:
            try:
                secs = int((datetime.fromisoformat(info["expires_at"]) -
                            utc_now()).total_seconds())
                text += f"  (expires in {secs}s)" if secs > 0 else "  (expired)"
            except Exception:
                logger.debug("Failed to parse OAuth2 token expiry", exc_info=True)
        self.auth_status_label.setText(text)
        self.auth_status_label.setStyleSheet(f"color: {color};")

