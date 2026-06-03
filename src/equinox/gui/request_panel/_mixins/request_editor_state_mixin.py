"""Editor-state and session-variable helpers for ``RequestPanel``."""
from __future__ import annotations

import logging
from typing import Any
from typing import cast

from equinox.application.requests import RequestEditorSnapshot
from equinox.core.validation import Validator

logger = logging.getLogger(__name__)


class RequestEditorStateMixin:
    """Capture editor state and manage request-panel session variables."""

    current_request: Any
    _auth: Any
    _inherited_auth: Any
    _inherited_auth_source: str | None
    _session_vars: dict[str, str]

    def get_session_vars(self: Any) -> dict[str, str]:
        """Return a copy of the current session variables."""
        return dict(self._session_vars)

    def get_interpolation_context(self: Any) -> dict[str, str]:
        """Return a combined interpolation context for helper panels."""
        context: dict[str, str] = dict(self._session_vars)
        try:
            path_params = cast(dict[str, str], dict(self.path_params_table.get_all_data()))
            context.update(path_params)
        except Exception:
            logger.exception("Failed to read path params for interpolation context", exc_info=True)
        return context

    def set_session_var(self: Any, key: str, value: str) -> None:
        """Set a session variable and notify listeners."""
        validated_key = Validator.validate_variable_name(key)
        self._session_vars[validated_key] = value
        self.session_vars_changed.emit(dict(self._session_vars))

    def delete_session_var(self: Any, key: str) -> bool:
        """Delete a session variable by key."""
        validated_key = Validator.validate_variable_name(key)
        if validated_key not in self._session_vars:
            return False
        del self._session_vars[validated_key]
        self.session_vars_changed.emit(dict(self._session_vars))
        return True

    def clear_session_vars(self: Any) -> None:
        """Clear all session variables and notify listeners."""
        self._session_vars.clear()
        self.session_vars_changed.emit({})

    def _serialize_auth_snapshot(self: Any, value: Any) -> tuple[str | None, dict[str, Any]]:
        """Return a safe ``(auth_type, auth_data)`` tuple for snapshotting."""
        if value is None:
            return None, {}
        auth_type = type(value).__name__
        to_dict = getattr(value, "to_dict", None)
        if not callable(to_dict):
            return auth_type, {}
        try:
            raw = to_dict()
        except Exception:
            logger.exception("Failed to serialize auth state for snapshot", exc_info=True)
            return auth_type, {}
        if not isinstance(raw, dict):
            return auth_type, {}
        return auth_type, cast(dict[str, Any], dict(raw))

    def _build_snapshot_auth_fields(self: Any) -> dict[str, Any]:
        """Collect auth-related snapshot fields."""
        auth_type, auth_data = self._serialize_auth_snapshot(self._auth)
        inherited_auth_type, inherited_auth_data = self._serialize_auth_snapshot(
            self._inherited_auth,
        )
        return {
            "auth_type": auth_type,
            "auth_data": auth_data,
            "inherited_auth_type": inherited_auth_type,
            "inherited_auth_data": inherited_auth_data,
            "inherited_auth_source": self._inherited_auth_source,
        }

    def _build_snapshot_payload_fields(self: Any) -> dict[str, Any]:
        """Collect request payload, rules, and editor content for the snapshot."""
        return {
            "headers": cast(dict[str, str], dict(self.headers_table.get_data())),
            "params": cast(dict[str, str], dict(self.params_table.get_enabled_data())),
            "params_list": cast(
                tuple[dict[str, Any], ...],
                tuple(dict(row) for row in self.params_table.get_all_rows()),
            ),
            "multipart_data": cast(
                tuple[dict[str, Any], ...],
                tuple(dict(row) for row in self._get_multipart_data()),
            ),
            "captures": cast(
                tuple[dict[str, Any], ...],
                tuple(dict(rule) for rule in self._get_captures()),
            ),
            "assertions": cast(
                tuple[dict[str, Any], ...],
                tuple(dict(rule) for rule in self._get_assertions()),
            ),
            "path_params": cast(dict[str, str], dict(self.path_params_table.get_all_data())),
            "body": self.body_text.toPlainText(),
            "body_type": self.body_type_combo.currentText(),
            "graphql_query": self._gql_query.toPlainText(),
            "graphql_variables": self._gql_vars.toPlainText(),
            "pre_script": self.pre_script_editor.toPlainText(),
            "post_script": self.post_script_editor.toPlainText(),
            "session_vars": dict(self._session_vars),
        }

    def _build_snapshot_request_fields(self: Any) -> dict[str, Any]:
        """Collect request identity and metadata fields for the snapshot."""
        request = self.current_request
        return {
            "method": self.method_combo.currentText(),
            "url": self.url_input.text().strip(),
            "timeout": float(self.timeout_spin.value()),
            "verify_ssl": bool(self.verify_ssl_check.isChecked()),
            "follow_redirects": bool(self.follow_redirects_check.isChecked()),
            "name": getattr(request, "name", None),
            "description": self.notes_editor.toPlainText().strip() or None,
            "collection_id": getattr(request, "collection_id", None),
            "folder": getattr(request, "folder", None),
            "request_id": getattr(request, "id", None),
            "cert_path": self.cert_path_input.text().strip() or None,
            "cert_key_path": self.cert_key_input.text().strip() or None,
        }

    def _build_request_editor_snapshot(self: Any) -> RequestEditorSnapshot:
        """Capture the current editor widget state as plain data."""
        return RequestEditorSnapshot(
            **self._build_snapshot_request_fields(),
            **self._build_snapshot_payload_fields(),
            **self._build_snapshot_auth_fields(),
        )

    def _clear_dirty(self: Any) -> None:
        """Clear the dirty flag and sync the visible editor state."""
        self._dirty = False
        self._sync_editor_state_ui()

    def _sync_editor_state_ui(self: Any) -> None:
        """Reflect scratch/saved/dirty state in the request footer."""
        save_button = getattr(self, "save_button", None)
        state_label = getattr(self, "_editor_state_label", None)
        has_saved_target = bool(getattr(self.current_request, "id", None))

        if self._dirty:
            if save_button is not None:
                save_button.setText("Save Changes")
                save_button.setToolTip("Save the current request changes to a collection")
            if state_label is not None:
                state_label.setText("Unsaved changes")
            return

        if save_button is not None:
            save_button.setText("Save")
            save_button.setToolTip("Save to a collection (prompts for name / folder)")
        if state_label is None:
            return
        state_label.setText("Saved to collection" if has_saved_target else "Scratch request")
