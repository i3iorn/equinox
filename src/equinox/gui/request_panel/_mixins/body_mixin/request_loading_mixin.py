"""Request loading and reset helpers for ``RequestPanel``."""

from __future__ import annotations

import logging
from typing import Any, cast

from PyQt6.QtWidgets import QWidget

from equinox.core.request import Request
from equinox.gui.request_panel._mixins.assertions_mixin import LABEL_EMPTY
from equinox.gui.workers import DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)


class RequestLoadingMixin:
    """Load persisted requests into the editor and reset the editor state."""

    current_request: Request | None
    _inherited_auth_source: str | None

    def _as_qwidget(self) -> QWidget:
        """Return ``self`` typed as ``QWidget`` for Qt APIs."""
        return cast(QWidget, cast(object, self))

    @staticmethod
    def _try_ui(fn: Any, *args: Any, **kwargs: Any) -> None:
        """Run a UI operation while ignoring missing C++ widget errors."""
        try:
            fn(*args, **kwargs)
        except RuntimeError:
            logger.debug("Widget unavailable in %s", getattr(fn, "__name__", fn), exc_info=True)

    def load_request(self: Any, request: Request) -> None:
        """Load a request into internal state and editor widgets."""
        self._set_request_state(request)
        self._resolve_auth_for_request()
        self._load_core_request_fields(request)
        self._load_body_and_multipart(request)
        self._load_auth_and_rules(request)
        self._load_scripts(request)
        self._load_certificates(request)
        self._load_settings(request)
        self._clear_script_results()
        self._load_notes(request)
        self._load_path_params(request)
        self._final_housekeeping()

    def _set_request_state(self: Any, request: Request) -> None:
        """Set internal request state before touching UI widgets."""
        self._auth = getattr(request, "auth", None)
        self.current_request = request

    def _resolve_auth_for_request(self: Any) -> None:
        """Resolve inherited auth if the loaded request has no own auth."""
        if self._auth is not None:
            self._inherited_auth = None
            self._inherited_auth_source = None
            return
        try:
            self._resolve_inherited_auth()
        except Exception:
            logger.exception("Failed to resolve inherited auth during load_request", exc_info=True)

    def _load_core_request_fields(self: Any, request: Request) -> None:
        """Populate URL, method, headers, and query parameters."""
        self._try_ui(self.url_input.setText, request.url)
        self._try_ui(self._set_method_from_request, request.method)
        self._try_ui(self.headers_table.set_data, request.headers or {})
        params_source = getattr(request, "params_list", None) or request.params or {}
        self._try_ui(self.params_table.set_data, params_source)

    def _set_method_from_request(self: Any, method: str) -> None:
        """Select the request method in the method combo box."""
        index = self.method_combo.findText(method)
        if index >= 0:
            self.method_combo.setCurrentIndex(index)

    def _load_body_and_multipart(self: Any, request: Request) -> None:
        """Populate body widgets from the loaded request payload."""
        multipart_data = getattr(request, "multipart_data", None)
        if multipart_data:
            self._try_ui(self._load_multipart_body, multipart_data)
            return
        if request.body:
            self._try_ui(self._load_plain_body, request)
            return
        self._try_ui(self._clear_body_widgets)

    def _load_multipart_body(self: Any, multipart_data: Any) -> None:
        """Load multipart rows and switch the body mode accordingly."""
        self._set_multipart_data(multipart_data)
        self.body_type_combo.setCurrentText("multipart/form-data")
        self.body_text.clear()

    def _load_plain_body(self: Any, request: Request) -> None:
        """Load a non-multipart body and infer its editor mode."""
        body_text = request.body if isinstance(request.body, str) else ""
        self.body_text.setPlainText(body_text)
        self._multipart_table.setRowCount(0)
        self.body_type_combo.setCurrentText(self._detect_body_type(body_text, request.headers))

    def _clear_body_widgets(self: Any) -> None:
        """Clear all body widgets and switch back to ``none`` mode."""
        self.body_text.clear()
        self._multipart_table.setRowCount(0)
        self.body_type_combo.setCurrentText("none")

    def _load_auth_and_rules(self: Any, request: Request) -> None:
        """Load auth state plus capture and assertion rules."""
        self._try_ui(self._update_auth_display, self._auth)
        self._try_ui(self._set_captures, getattr(request, "captures", None) or [])
        self._try_ui(self._set_assertions, getattr(request, "assertions", None) or [])

    def _load_scripts(self: Any, request: Request) -> None:
        """Load pre-request and post-response scripts."""
        self._try_ui(self.pre_script_editor.setPlainText, getattr(request, "pre_script", "") or "")
        self._try_ui(
            self.post_script_editor.setPlainText,
            getattr(request, "post_script", "") or "",
        )

    def _load_certificates(self: Any, request: Request) -> None:
        """Load client certificate paths."""
        self._try_ui(self.cert_path_input.setText, getattr(request, "cert_path", "") or "")
        self._try_ui(self.cert_key_input.setText, getattr(request, "cert_key_path", "") or "")

    def _load_settings(self: Any, request: Request) -> None:
        """Load request execution settings."""
        timeout = getattr(request, "timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT
        self._try_ui(self.timeout_spin.setValue, timeout)
        self._try_ui(self.verify_ssl_check.setChecked, bool(getattr(request, "verify_ssl", True)))
        self._try_ui(
            self.follow_redirects_check.setChecked,
            bool(getattr(request, "follow_redirects", True)),
        )

    def _clear_script_results(self: Any) -> None:
        """Clear the pre/post script result labels."""
        self._try_ui(self.pre_script_result.setText, "")
        self._try_ui(self.post_script_result.setText, "")

    def _load_notes(self: Any, request: Request) -> None:
        """Load request notes into the notes editor."""
        self._try_ui(self.notes_editor.setPlainText, getattr(request, "description", "") or "")

    def _load_path_params(self: Any, request: Request) -> None:
        """Load path parameters and sync their section visibility."""
        self._try_ui(self.path_params_table.set_data, getattr(request, "path_params", None) or {})
        self._try_ui(self.path_params_table.update_from_url, request.url)
        self._try_ui(
            self._path_params_widget.setVisible,
            self.path_params_table.rowCount() > 0,
        )

    def _final_housekeeping(self: Any) -> None:
        """Finish request loading by clearing dirty state and refreshing badges."""
        self._try_ui(self._clear_dirty)
        self._try_ui(self._update_tab_labels)
        self._try_ui(self._update_url_suffix)

    def clear(self: Any) -> None:
        """Reset the request editor to its default scratch-request state."""
        try:
            if self._worker is not None and self._worker.isRunning():
                self._cancel_request()
        except RuntimeError:
            pass
        self._try_ui(self._reset_request_widgets)
        self.current_request = None
        self._try_ui(self._clear_dirty)
        self._try_ui(self._update_tab_labels)
        self._try_ui(self._update_url_suffix)

    def _reset_request_widgets(self: Any) -> None:
        """Reset all request widgets except session variables and auth."""
        self.url_input.clear()
        self.method_combo.setCurrentIndex(0)
        self.headers_table.reset()
        self.params_table.reset()
        self.path_params_table.reset()
        self._path_params_widget.setVisible(False)
        self.body_text.clear()
        self._multipart_table.setRowCount(0)
        self._gql_query.clear()
        self._gql_vars.clear()
        self.body_type_combo.setCurrentIndex(0)
        self.captures_table.setRowCount(0)
        self.captures_results_label.setText(LABEL_EMPTY)
        self.assertions_table.setRowCount(0)
        self.assertions_results_label.setText(LABEL_EMPTY)
        self._reset_assertions_tab_title()
        self.pre_script_editor.clear()
        self.post_script_editor.clear()
        self.pre_script_result.setText("")
        self.post_script_result.setText("")
        self.cert_path_input.clear()
        self.cert_key_input.clear()
        self.timeout_spin.setValue(DEFAULT_TIMEOUT)
        self.verify_ssl_check.setChecked(True)
        self.follow_redirects_check.setChecked(True)
        self.notes_editor.clear()

    def _reset_assertions_tab_title(self: Any) -> None:
        """Restore the Assertions tab title after clearing the editor."""
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index).startswith("Assertions"):
                self.tabs.setTabText(index, "Assertions")
                return
