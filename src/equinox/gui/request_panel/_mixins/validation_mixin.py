"""Real-time validation helpers for request-panel editor fields."""

from __future__ import annotations

import json
import logging
from typing import Any
from typing import cast

from equinox.core.exceptions import ValidationError
from equinox.core.validation import Validator
from equinox.security import redact_url
from PyQt6.QtCore import QObject
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QPlainTextEdit
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

_MAX_SYNC_JSON_VALIDATE_BYTES = 5_000_000


class _RequestValidationMixin:
    """Provide debounced inline validation for request input widgets."""

    url_input: QLineEdit
    headers_table: Any
    body_text: QPlainTextEdit
    body_type_combo: QComboBox
    _gql_vars: QPlainTextEdit
    send_button: QPushButton
    _validation_timer: QTimer
    _url_valid: bool
    _headers_valid: bool
    _body_valid: bool

    def _init_validation(self) -> None:
        """Set up validation signal handlers for all input fields."""
        self._validation_timer = QTimer(cast(QObject, cast(object, self)))
        self._validation_timer.setInterval(300)
        self._validation_timer.timeout.connect(self._run_validation_checks)
        self._validation_timer.setSingleShot(True)

        self._url_valid = True
        self._headers_valid = True
        self._body_valid = True

        self.url_input.textChanged.connect(self._schedule_validation)
        self.headers_table.itemChanged.connect(self._schedule_validation)
        self.body_text.textChanged.connect(self._schedule_validation)

        logger.debug("Real-time validation initialized")

    def _schedule_validation(self) -> None:
        """Schedule validation check (debounced)."""
        self._validation_timer.stop()
        self._validation_timer.start()

    def _run_validation_checks(self) -> None:
        """Perform all active validation checks."""
        self._validate_url()
        self._validate_headers()
        self._validate_body()
        self._update_send_button_state()

    def _validate_url(self) -> None:
        """Validate URL and show inline feedback."""
        url_text = self.url_input.text().strip()

        if not url_text:
            self._set_field_valid(self.url_input, None)
            if hasattr(self, "_set_url_validation_hint"):
                self._set_url_validation_hint("")
            if hasattr(self, "_set_url_fix_suggestion"):
                self._set_url_fix_suggestion(None)
            self._url_valid = True
            return

        if "{{" in url_text and "}}" in url_text:
            self._set_field_valid(self.url_input, None)
            if hasattr(self, "_set_url_validation_hint"):
                self._set_url_validation_hint(
                    "URL contains template variables; resolved at send time.",
                )
            if hasattr(self, "_set_url_fix_suggestion"):
                self._set_url_fix_suggestion(None)
            self._url_valid = True
            return

        try:
            Validator.validate_resolved_url(url_text)
            self._set_field_valid(self.url_input, "valid")
            if hasattr(self, "_set_url_validation_hint"):
                self._set_url_validation_hint("URL looks valid.")
            if hasattr(self, "_set_url_fix_suggestion"):
                self._set_url_fix_suggestion(None)
            self._url_valid = True
            logger.debug("URL validation passed: %s", redact_url(url_text)[:50])
        except ValidationError as exc:
            err_msg = str(exc)
            self._set_field_valid(self.url_input, "error", err_msg)
            if hasattr(self, "_set_url_validation_hint"):
                self._set_url_validation_hint(err_msg, is_error=True)
            fix = self._suggest_url_fix(url_text)
            if hasattr(self, "_set_url_fix_suggestion"):
                if fix is None:
                    self._set_url_fix_suggestion(None)
                else:
                    fixed_url, reason = fix
                    self._set_url_fix_suggestion(fixed_url, reason)
            self._url_valid = False
            logger.debug("URL validation failed: %s", err_msg)

    @staticmethod
    def _suggest_url_fix(url_text: str) -> tuple[str, str] | None:
        """Return a safe URL correction suggestion, if one is obvious."""
        text = (url_text or "").strip()
        if not text:
            return None
        if url_text != text:
            return text, "Trim leading/trailing whitespace"
        if any(ch.isspace() for ch in text):
            encoded = (
                text.replace(" ", "%20")
                .replace("\t", "%20")
                .replace("\n", "%20")
                .replace("\r", "%20")
            )
            if encoded != text:
                return encoded, "Encode whitespace as %20"
        if not text.lower().startswith(("http://", "https://")):
            if text.startswith("//"):
                return f"https:{text}", "Add https scheme"
            return f"https://{text}", "Add https scheme"
        return None

    def _validate_headers(self) -> None:
        """Validate headers and show inline feedback."""
        try:
            headers_dict = self.headers_table.get_data() or {}
            if not headers_dict:
                self._set_field_valid(self.headers_table, None)
                self._headers_valid = True
                return

            Validator.validate_headers(headers_dict, strict=False)
            self._set_field_valid(self.headers_table, "valid")
            self._headers_valid = True
            logger.debug("Headers validation passed")
        except (ValidationError, ValueError) as exc:
            self._set_field_valid(self.headers_table, "error", str(exc))
            self._headers_valid = False
            logger.debug("Headers validation failed: %s", str(exc))

    def _mark_valid(self, state: str | None) -> None:
        """Mark the body field as valid or neutral."""
        self._set_field_valid(self.body_text, state)

    def _mark_invalid(self, message: str) -> None:
        """Mark the body field as invalid with an error message."""
        self._set_field_valid(self.body_text, "error", message)

    def _set_field_valid(self, field: QWidget, status: str | None, message: str = "") -> None:
        """Display validation status on field with visual feedback.

        Args:
            field: Input widget to style
            status: "valid" (green ✓), "error" (red ✗), None (reset to default)
            message: Tooltip message for error details
        """
        field.setObjectName(
            "field-valid" if status == "valid" else "field-error" if status == "error" else "",
        )
        if status == "valid":
            field.setToolTip("✓ Valid input")
        elif status == "error":
            field.setToolTip(f"✗ {message}")
        else:
            field.setToolTip("")

    def _update_send_button_state(self) -> None:
        """Enable/disable Send button based on validation state."""
        url_text = self.url_input.text().strip()

        if not url_text:
            self.send_button.setEnabled(False)
            return

        if not self._url_valid:
            self.send_button.setEnabled(False)
            return

        if not self._headers_valid or not self._body_valid:
            logger.debug(
                "request_panel.validation.send_gate headers_valid=%s body_valid=%s",
                self._headers_valid,
                self._body_valid,
            )
            if not self._body_valid:
                self.send_button.setEnabled(False)
                return

        self.send_button.setEnabled(True)

    @staticmethod
    def _safe_text(widget: Any) -> str:
        try:
            value = widget.toPlainText()
            return value.strip() if isinstance(value, str) else ""
        except Exception:
            return ""

    @staticmethod
    def _safe_body_type(self_ref: Any) -> str:
        try:
            value = self_ref.body_type_combo.currentText()
            return value.lower() if isinstance(value, str) else ""
        except Exception:
            return ""

    @staticmethod
    def _is_json_mode(body_type: str) -> bool:
        return "json" in body_type

    @staticmethod
    def _is_graphql_mode(body_type: str) -> bool:
        return "graphql" in body_type

    def _validate_body(self) -> None:
        """Validate the active request body payload."""
        body_text = self._safe_text(self.body_text)
        body_type = self._safe_body_type(self)

        if not self._is_json_mode(body_type) and not self._is_graphql_mode(body_type):
            self._mark_valid(None)
            self._body_valid = True
            return

        if self._is_graphql_mode(body_type):
            self._body_valid = self._validate_graphql_variables()
            return

        self._body_valid = self._validate_json_body(body_text)

    def _validate_graphql_variables(self) -> bool:
        """Validate GraphQL variables when the editor is in GraphQL mode."""
        gql_vars = self._safe_text(self._gql_vars)

        if not gql_vars:
            self._mark_valid(None)
            return True

        if len(gql_vars) > _MAX_SYNC_JSON_VALIDATE_BYTES:
            logger.warning(
                "request_panel.validation.graphql_vars_skip_large op=validate_body size=%d",
                len(gql_vars),
            )
            self._mark_valid(None)
            return True

        try:
            json.loads(gql_vars)
            self._mark_valid("valid")
            logger.debug("GraphQL variables JSON validation passed")
            return True

        except json.JSONDecodeError as exc:
            msg = f"Invalid GraphQL variables JSON at line {exc.lineno}, col {exc.colno}: {exc.msg}"
            self._mark_invalid(msg)
            logger.debug("GraphQL variables JSON validation failed: %s", msg)
            return False

    def _validate_json_body(self, body_text: str) -> bool:
        """Validate JSON body content when the editor is in JSON mode."""
        if not body_text:
            self._mark_valid(None)
            return True

        if len(body_text) > _MAX_SYNC_JSON_VALIDATE_BYTES:
            logger.warning(
                "request_panel.validation.json_skip_large op=validate_body size=%d",
                len(body_text),
            )
            self._mark_valid(None)
            return True

        try:
            json.loads(body_text)
            self._mark_valid("valid")
            logger.debug("JSON body validation passed")
            return True

        except json.JSONDecodeError as exc:
            msg = f"Invalid JSON at line {exc.lineno}, col {exc.colno}: {exc.msg}"
            self._mark_invalid(msg)
            logger.debug("JSON body validation failed: %s", msg)
            return False
