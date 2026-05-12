"""Shared OAuth2 token test workflow for credential dialogs."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from equinox.gui.dialogs._oauth_form_utils import parse_json_object_field_lenient
from equinox.gui.workers import OAuthTokenTester

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QPushButton


class OAuthConnectionTestMixin:
    """Mixin that centralizes OAuth2 test-connection behavior.

    Expected attributes on subclasses:
    - ``test_btn``
    - ``view_response_btn``
    - ``_tester``
    - ``_last_test_response``
    - ``_set_status(msg, ok)``
    """

    test_btn: "QPushButton"
    view_response_btn: "QPushButton"
    _tester: Optional[OAuthTokenTester]
    _last_test_response: Optional[dict]
    _test_btn_idle_text: str
    _test_btn_busy_text: str
    @staticmethod
    def _require_token_fields(token_url: str, client_id: str) -> Optional[str]:
        if not token_url.strip() or not client_id.strip():
            return "Token URL and Client ID are required to test the connection."
        return None

    def _open_token_response(self) -> None:
        if not self._last_test_response:
            return
        # Deferred import to avoid circular imports between sibling dialogs.
        from equinox.gui.dialogs.auth_dialog import _TokenResponseDialog

        dlg = _TokenResponseDialog(self._last_test_response, self)  # type: ignore[arg-type]
        dlg.exec()


    def _set_status(self, msg: str, ok: Optional[bool]) -> None:
        """Implemented by ``DirtyDialogMixin`` in concrete dialog classes."""
        raise NotImplementedError

    def _start_oauth_test(
        self,
        *,
        token_url: str,
        client_id: str,
        secret: str,
        scope: str,
        grant_type: str,
        extra_raw: str,
        token_auth: Optional[str] = None,
    ) -> None:
        """Start an asynchronous OAuth token test from form values."""
        missing = self._require_token_fields(token_url, client_id)
        if missing:
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Missing Fields", missing)  # type: ignore[arg-type]
            return

        extra_params = parse_json_object_field_lenient(extra_raw)

        self.test_btn.setEnabled(False)
        self.test_btn.setText(getattr(self, "_test_btn_busy_text", "Testing..."))
        self._set_status("Connecting...", ok=None)
        self._last_test_response = None
        self.view_response_btn.setEnabled(False)

        kwargs = {}
        if token_auth is not None:
            kwargs["token_auth"] = token_auth

        self._tester = OAuthTokenTester(
            token_url,
            client_id,
            secret,
            scope,
            grant_type,
            extra_params,
            **kwargs,
        )
        self._tester.done.connect(self._on_oauth_test_done)
        self._tester.start()

    def _on_oauth_test_done(self, success: bool, message: str, response: object) -> None:
        """Apply common UI state updates once an OAuth token test completes."""
        self.test_btn.setEnabled(True)
        self.test_btn.setText(getattr(self, "_test_btn_idle_text", "Test Connection"))
        self._last_test_response = response if isinstance(response, dict) else None
        self.view_response_btn.setEnabled(self._last_test_response is not None)
        self._set_status(message, ok=success)

    def _view_oauth_test_response(self) -> None:
        """Open the common token-response dialog for the last test run."""
        self._open_token_response()






