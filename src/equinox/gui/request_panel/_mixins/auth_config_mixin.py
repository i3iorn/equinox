"""Auth configuration and auth-tab UI helpers for ``RequestPanel``."""
from __future__ import annotations

import logging
from typing import Any
from typing import TYPE_CHECKING

from equinox.gui.request_panel._constants import AUTH_TAB_MARGINS
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

_AUTH_NONE_LABEL = "Auth: None"
_AUTH_NONE_DESC = "No authentication configured"
_BTN_CONFIGURE = "Configure Authentication"
_BTN_CLEAR = "Clear Auth"


class AuthConfigMixin:
    """Own the auth-tab UI and dialog result application."""

    current_request: Any
    _request_persistence: Any
    _auth: Any | None
    _inherited_auth: Any | None
    _inherited_auth_source: str | None
    db: Any

    if TYPE_CHECKING:

        def _auth_configs_match(self, left: Any, right: Any) -> bool: ...
        def _update_auth_display(self, auth: Any = None) -> None: ...
        def _resolve_inherited_auth(self) -> None: ...
        def _mark_dirty(self) -> None: ...

    def _save_inherited_token_to_source(self, auth: Any) -> None:
        """Persist a freshly fetched inherited token back to its source."""
        source = getattr(self, "_inherited_auth_source", None)
        request = self.current_request
        if not source or not request or not getattr(request, "collection_id", None):
            logger.debug("No inherited auth source available for token persistence")
            return
        try:
            self._request_persistence.persist_auth_to_source(request.collection_id, source, auth)
        except Exception as exc:
            logger.debug("Failed to save dialog token to inherited source: %s", exc, exc_info=True)

    def _create_auth_tab(self) -> QWidget:
        """Create the authentication tab UI."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(*AUTH_TAB_MARGINS)

        self.auth_type_label = self._create_styled_label(_AUTH_NONE_LABEL, bold=True)
        self.auth_details_label = self._create_styled_label(_AUTH_NONE_DESC, muted=True, wrap=True)
        self.auth_status_label = self._create_styled_label("", wrap=True)
        self.auth_trust_label = self._create_styled_label("", muted=True, wrap=True)

        layout.addWidget(self.auth_type_label)
        layout.addWidget(self.auth_details_label)
        layout.addWidget(self.auth_status_label)
        layout.addWidget(self.auth_trust_label)
        layout.addLayout(self._create_auth_button_row())
        layout.addStretch()
        return widget

    @staticmethod
    def _create_styled_label(
        text: str,
        *,
        bold: bool = False,
        muted: bool = False,
        wrap: bool = False,
    ) -> QLabel:
        """Create a consistently styled auth label."""
        label = QLabel(text)
        if bold:
            label.setObjectName("boldLabel")
        if muted:
            label.setObjectName("mutedLabel")
        if wrap:
            label.setWordWrap(True)
        return label

    def _create_auth_button_row(self) -> QHBoxLayout:
        """Create the Configure / Clear auth button row."""
        row = QHBoxLayout()
        configure_button = QPushButton(_BTN_CONFIGURE)
        configure_button.clicked.connect(self._configure_auth)
        clear_button = QPushButton(_BTN_CLEAR)
        clear_button.clicked.connect(self._clear_auth)
        row.addWidget(configure_button)
        row.addWidget(clear_button)
        row.addStretch()
        return row

    def _configure_auth(self) -> None:
        """Show the auth dialog and apply any saved changes."""
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        was_inherited = self._auth is None and self._inherited_auth is not None
        display_auth = self._auth or self._inherited_auth
        dialog = AuthDialog(display_auth, self, db=self.db)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        saved_auth = getattr(dialog, "_saved_auth", None)
        fetched_token = getattr(dialog, "_last_fetched_auth", None)
        if was_inherited and saved_auth is not None:
            self._handle_inherited_auth_dialog_result(saved_auth, fetched_token)
            return
        self._handle_own_auth_dialog_result(saved_auth)

    def _handle_inherited_auth_dialog_result(self, saved: Any, fetched_token: Any) -> None:
        """Apply dialog results while the request is using inherited auth."""
        if not self._auth_configs_match(saved, self._inherited_auth):
            self._handle_own_auth_dialog_result(saved)
            return
        if not fetched_token:
            logger.debug("Auth dialog unchanged inherited config with no token fetch")
            return
        self._inherited_auth = saved
        self._save_inherited_token_to_source(saved)
        self._update_auth_display(self._auth)

    def _handle_own_auth_dialog_result(self, saved: Any) -> None:
        """Apply dialog results as request-owned auth."""
        old_auth = self._auth
        self._auth = saved
        if self._auth is not None:
            self._inherited_auth = None
            self._inherited_auth_source = None
        else:
            self._resolve_inherited_auth()
        self._update_auth_display(self._auth)
        if self._auth_configs_match(old_auth, self._auth):
            return
        self._mark_dirty()
        logger.debug("Auth dialog changed request auth state")

    def _clear_auth(self) -> None:
        """Clear request-owned auth and fall back to inherited auth."""
        had_auth = self._auth is not None
        self._auth = None
        self._resolve_inherited_auth()
        self._update_auth_display(None)
        if had_auth:
            self._mark_dirty()
            logger.debug("Cleared request-owned auth")
