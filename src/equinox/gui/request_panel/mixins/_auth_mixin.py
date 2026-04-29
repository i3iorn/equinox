"""Auth configuration and display mixin for RequestPanel.

Contains ``_RequestAuthMixin`` — all methods related to authentication
configuration, display rendering, inheritance resolution, and token
persistence back to collection/folder sources.

Auth display and preflight checks now delegate to each strategy's
``get_display_summary()`` and ``get_preflight_warning()`` methods,
eliminating the isinstance dispatch chains that required updates
every time a new auth type was added.

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

from equinox.auth import OAuth2Auth
from equinox.core.security import mask_secret
from equinox.core.time import utc_now
from equinox.gui.theme import Colors

from equinox.gui.request_panel._constants import (
    AUTH_TAB_MARGINS,
    AUTH_VOLATILE_KEYS,
    FOLDER_AUTH_PREFIX,
)
from equinox.gui.request_panel.mixins._helpers import write_auth_to_source

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# UI Constants
# ──────────────────────────────────────────────────────────────────────────────

_AUTH_NONE_LABEL = "Auth: None"
_AUTH_NONE_DESC = "No authentication configured"
_AUTH_TOKEN_NONE = "Token: None"
_AUTH_TOKEN_EXPIRING = "Token: Expiring soon"
_AUTH_TOKEN_VALID = "Token: Valid"
_AUTH_INHERITED_FROM_COLLECTION = "  (inherited from collection)"
_AUTH_INHERITED_FROM_FOLDER = '  (inherited from folder "{}")'

_BTN_CONFIGURE = "Configure Authentication…"
_BTN_CLEAR = "Clear Auth"


class _RequestAuthMixin:
    """Methods for authentication configuration, display, and persistence.

    Responsible for:
    - Auth tab UI creation
    - Auth configuration dialog interaction
    - Auth display (delegated to strategy metadata)
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
            logger.debug("No inherited auth source; token not persisted")
            return
        req = self.current_request
        if not req or not req.collection_id:
            logger.debug("No current request or collection_id; token not persisted")
            return
        try:
            write_auth_to_source(self._collection_mgr, req.collection_id, source, auth)
            logger.debug("Saved dialog-fetched token to source: %s", mask_secret(source))
        except Exception as exc:
            logger.debug("Failed to save dialog token to source: %s", exc, exc_info=True)

    # ── Tab creation ──────────────────────────────────────────────────

    def _create_auth_tab(self) -> QWidget:
        """Create the authentication configuration tab.

        Returns:
            QWidget with auth labels and configure/clear buttons
        """
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(*AUTH_TAB_MARGINS)

        # Create labels with initial state
        self.auth_type_label = self._create_styled_label(
            _AUTH_NONE_LABEL, bold=True
        )
        self.auth_details_label = self._create_styled_label(
            _AUTH_NONE_DESC, muted=True, wrap=True
        )
        self.auth_status_label = self._create_styled_label("", wrap=True)

        # Create button row
        btn_row = self._create_auth_button_row()

        # Assemble layout
        layout.addWidget(self.auth_type_label)
        layout.addWidget(self.auth_details_label)
        layout.addWidget(self.auth_status_label)
        layout.addLayout(btn_row)
        layout.addStretch()
        return w

    @staticmethod
    def _create_styled_label(
        text: str, bold: bool = False, muted: bool = False, wrap: bool = False
    ) -> QLabel:
        """Create a styled QLabel (DRY helper).

        Args:
            text: Label text
            bold: Whether to make the text bold
            muted: Whether to apply muted style (mutedLabel object name)
            wrap: Whether to enable word wrapping

        Returns:
            Configured QLabel instance
        """
        label = QLabel(text)
        if bold:
            label.setObjectName("boldLabel")
        if muted:
            label.setObjectName("mutedLabel")
        if wrap:
            label.setWordWrap(True)
        return label

    def _create_auth_button_row(self) -> QHBoxLayout:
        """Create the auth button row (Configure / Clear) (DRY helper).

        Returns:
            QHBoxLayout with configure and clear buttons
        """
        btn_row = QHBoxLayout()
        configure_btn = QPushButton(_BTN_CONFIGURE)
        configure_btn.clicked.connect(self._configure_auth)
        clear_btn = QPushButton(_BTN_CLEAR)
        clear_btn.clicked.connect(self._clear_auth)
        btn_row.addWidget(configure_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        return btn_row

    # ── Configuration ─────────────────────────────────────────────────

    def _configure_auth(self) -> None:
        """Show auth dialog and apply the user's auth configuration changes."""
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        # Determine what auth to display in dialog
        was_inherited = self._auth is None and self._inherited_auth is not None
        display_auth = self._auth or self._inherited_auth

        # Show dialog
        dialog = AuthDialog(display_auth, self, db=self.db)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Extract saved auth and any token that was fetched
        saved = getattr(dialog, "_saved_auth", None)
        fetched_token = getattr(dialog, "_last_fetched_auth", None)

        # Dispatch on the dialog result
        if was_inherited and saved is not None:
            self._handle_inherited_auth_dialog_result(saved, fetched_token)
        else:
            self._handle_own_auth_dialog_result(saved)

    def _handle_inherited_auth_dialog_result(
        self, saved: Any, fetched_token: Any
    ) -> None:
        """Handle auth dialog result when the request was using inherited auth.

        Carefully distinguishes between:
        - Case A: Unchanged config, no token fetch → no-op
        - Case B: Unchanged config, token fetched → persist at source only
        - Case C: Changed config or explicit selection → set as own auth

        Args:
            saved: Auth object from the dialog
            fetched_token: Newly fetched token (if any)
        """
        if not self._auth_configs_match(saved, self._inherited_auth):
            # Case C: Config changed; set as own auth
            self._handle_own_auth_dialog_result(saved)
            return

        if not fetched_token:
            # Case A: No change, no fetch; no-op
            logger.debug("Auth dialog: unchanged inherited config, no token fetch")
            return

        # Case B: Unchanged config but token was fetched; persist at source
        logger.debug("Auth dialog: persisting fetched token to inherited auth source")
        self._inherited_auth = saved
        self._save_inherited_token_to_source(saved)
        self._update_auth_display(self._auth)  # self._auth still None

    def _handle_own_auth_dialog_result(self, saved: Any) -> None:
        """Handle auth dialog result when setting own auth on the request.

        Args:
            saved: Auth object from dialog (may be None for "No Auth")
        """
        old_auth = self._auth
        self._auth = saved

        # Update inheritance and mark dirty only if config changed
        if self._auth is not None:
            # Own auth supersedes inherited
            self._inherited_auth = None
            self._inherited_auth_source = None
        else:
            # User chose "No Auth" — re-resolve inherited
            self._resolve_inherited_auth()

        self._update_auth_display(self._auth)
        if not self._auth_configs_match(old_auth, self._auth):
            self._mark_dirty()
            logger.debug("Auth dialog: marked request dirty (config changed)")

    def _clear_auth(self) -> None:
        """Clear own auth on this request; re-resolve inherited auth."""
        had_auth = self._auth is not None
        self._auth = None
        self._resolve_inherited_auth()
        self._update_auth_display(None)
        if had_auth:
            self._mark_dirty()
            logger.debug("Auth cleared")

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
        """Build a human-readable '(inherited from …)' suffix.

        Args:
            source: Source identifier or None

        Returns:
            Human-readable suffix, or empty string if no source
        """
        if not source:
            return ""
        if source.startswith(FOLDER_AUTH_PREFIX):
            folder = source[len(FOLDER_AUTH_PREFIX):]
            return _AUTH_INHERITED_FROM_FOLDER.format(folder)
        if source == "collection":
            return _AUTH_INHERITED_FROM_COLLECTION
        return ""

    def _update_auth_display(self, auth: Any = None) -> None:
        """Update the auth tab labels to reflect current auth state.

        Uses strategy metadata (``DISPLAY_NAME``, ``get_display_summary()``)
        instead of isinstance dispatch. OAuth2 gets special treatment for
        its token-status line with expiry countdown.

        Args:
            auth: Own auth strategy or None
        """
        # Reset status line (used only by OAuth2)
        self.auth_status_label.setText("")

        # Determine what to display: own auth or inherited
        display_auth = auth or self._get_inherited_auth_safe()
        if not display_auth:
            self._set_auth_display_none()
            return

        # Get inherited label if applicable
        inherited_label = ""
        if auth is None and self._inherited_auth_source:
            inherited_label = self._format_inherited_label(self._inherited_auth_source)

        self._set_auth_display_for_strategy(display_auth, inherited_label)

    def _get_inherited_auth_safe(self) -> Optional[Any]:
        """Safely retrieve inherited auth (handles missing attributes).

        Returns:
            Inherited auth object or None
        """
        return getattr(self, "_inherited_auth", None)

    def _set_auth_display_none(self) -> None:
        """Set display labels for the 'no auth' state."""
        self.auth_type_label.setText(_AUTH_NONE_LABEL)
        self.auth_details_label.setText(_AUTH_NONE_DESC)

    def _set_auth_display_for_strategy(self, auth: Any, inherited_label: str) -> None:
        """Set display labels for a specific auth strategy.

        Args:
            auth: Auth strategy object
            inherited_label: Inherited source label (e.g. " (inherited from collection)")
        """
        # Type label from strategy's DISPLAY_NAME or class name
        type_name = self._get_auth_type_name(auth)
        self.auth_type_label.setText(f"Auth: {type_name}{inherited_label}")

        # Details from strategy's get_display_summary()
        summary = self._get_auth_display_summary(auth)
        self.auth_details_label.setText(summary)

        # Special OAuth2 token-status line
        if isinstance(auth, OAuth2Auth):
            self._render_oauth2_token_status(auth)

    @staticmethod
    def _get_auth_type_name(auth: Any) -> str:
        """Extract type name from auth strategy (DRY helper).

        Tries DISPLAY_NAME attribute first, falls back to class name.

        Args:
            auth: Auth strategy object

        Returns:
            Display name for the auth type
        """
        return getattr(auth, "DISPLAY_NAME", None) or type(auth).__name__

    @staticmethod
    def _get_auth_display_summary(auth: Any) -> str:
        """Extract display summary from auth strategy (DRY helper).

        Args:
            auth: Auth strategy object

        Returns:
            Display summary, or empty string if not available
        """
        if hasattr(auth, "get_display_summary"):
            try:
                return auth.get_display_summary()
            except Exception as exc:
                logger.debug("Failed to get display summary: %s", exc, exc_info=True)
        return ""

    def _render_oauth2_token_status(self, auth: OAuth2Auth) -> None:
        """Render the OAuth2 token status line with colour and countdown.

        Displays token state (None/Expiring/Valid) and time-to-expiry if available.

        Args:
            auth: OAuth2Auth instance with token info
        """
        try:
            info = auth.get_token_info()
            text, color = self._build_oauth2_status_text(auth, info)
            text = self._append_oauth2_expiry_countdown(text, info)
            self.auth_status_label.setText(text)
        except Exception as exc:
            logger.debug("Failed to render OAuth2 token status: %s", exc, exc_info=True)

    @staticmethod
    def _build_oauth2_status_text(auth: OAuth2Auth, info: dict) -> tuple[str, str]:
        """Build the base OAuth2 status text and color (DRY helper).

        Args:
            auth: OAuth2Auth instance
            info: Token info dict from get_token_info()

        Returns:
            Tuple of (status_text, color_code)
        """
        if not auth.access_token:
            return _AUTH_TOKEN_NONE, Colors.RED
        elif info.get("needs_refresh"):
            token_display = mask_secret(info.get("access_token", ""))
            return f"{_AUTH_TOKEN_EXPIRING}  [{token_display}]", Colors.AMBER
        else:
            token_display = mask_secret(info.get("access_token", ""))
            return f"{_AUTH_TOKEN_VALID}  [{token_display}]", Colors.GREEN

    @staticmethod
    def _append_oauth2_expiry_countdown(text: str, info: dict) -> str:
        """Append time-to-expiry countdown to OAuth2 status text (DRY helper).

        Args:
            text: Base status text
            info: Token info dict

        Returns:
            Text with expiry countdown appended (if available)
        """
        expires_at = info.get("expires_at")
        if not expires_at or not isinstance(expires_at, str):
            return text
        try:
            expiry_dt = datetime.fromisoformat(expires_at)
            secs = int((expiry_dt - utc_now()).total_seconds())
            if secs > 0:
                text += f"  (expires in {secs}s)"
            else:
                text += "  (expired)"
        except (ValueError, TypeError) as exc:
            logger.debug("Failed to parse OAuth2 token expiry: %s", exc)
        return text

