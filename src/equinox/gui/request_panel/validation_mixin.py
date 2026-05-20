"""Real-time validation for request fields in RequestPanel.

Provides inline validation feedback as users type in URL, headers, and body fields.
Shows validation status with visual indicators and prevents send if critical errors exist.
"""

import json
import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

from equinox.core.exceptions import ValidationError
from equinox.core.validation import Validator

logger = logging.getLogger(__name__)

_MAX_SYNC_JSON_VALIDATE_BYTES = 5_000_000


class _RequestValidationMixin:
    """Real-time validation for request input fields.

    Provides:
    - URL validation (scheme, length, format)
    - Headers validation (CRLF prevention, size limits)
    - JSON body validation (syntax checking)
    - Visual feedback (green/red borders, tooltips)
    - Send button enabling/disabling based on validation state
    """

    def _init_validation(self) -> None:
        """Set up validation signal handlers for all input fields."""
        # Debounce timer to avoid validating on every keystroke
        self._validation_timer = QTimer(self)
        self._validation_timer.setInterval(300)  # 300ms debounce
        self._validation_timer.timeout.connect(self._run_validation_checks)
        self._validation_timer.setSingleShot(True)

        # Track validation state
        self._url_valid = True
        self._headers_valid = True
        self._body_valid = True

        # Connect field changes to trigger validation
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

        # Empty is OK (user hasn't entered anything yet)
        if not url_text:
            self._set_field_valid(self.url_input, None)
            if hasattr(self, "_set_url_validation_hint"):
                self._set_url_validation_hint("")
            if hasattr(self, "_set_url_fix_suggestion"):
                self._set_url_fix_suggestion(None)
            self._url_valid = True
            return

        # Skip validation if URL contains unresolved variables
        if "{{" in url_text and "}}" in url_text:
            self._set_field_valid(self.url_input, None)  # Don't validate templates
            if hasattr(self, "_set_url_validation_hint"):
                self._set_url_validation_hint(
                    "URL contains template variables; resolved at send time."
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
            logger.debug("URL validation passed: %s", url_text[:50])
        except ValidationError as e:
            err_msg = str(e)
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
            logger.debug("URL validation failed: %s", str(e))

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
            # Get all headers from table
            headers_dict = self.headers_table.get_data() or {}
            if not headers_dict:
                self._set_field_valid(self.headers_table, None)
                self._headers_valid = True
                return

            Validator.validate_headers(headers_dict, strict=False)
            self._set_field_valid(self.headers_table, "valid")
            self._headers_valid = True
            logger.debug("Headers validation passed")
        except (ValidationError, ValueError) as e:
            self._set_field_valid(self.headers_table, "error", str(e))
            self._headers_valid = False
            logger.debug("Headers validation failed: %s", str(e))

    def _validate_body(self) -> None:
        """Validate JSON body if Content-Type is JSON."""
        body_text = self.body_text.toPlainText().strip()

        body_type = ""
        try:
            body_type = self.body_type_combo.currentText().lower()
        except Exception:
            body_type = ""

        is_json_mode = "json" in body_type
        is_graphql_mode = "graphql" in body_type

        # Non-JSON body modes are always considered valid here.
        if not is_json_mode and not is_graphql_mode:
            self._set_field_valid(self.body_text, None)
            self._body_valid = True
            return

        # GraphQL variables are JSON; validate the variables editor directly.
        if is_graphql_mode:
            gql_vars = ""
            try:
                gql_vars = self._gql_vars.toPlainText().strip()
            except Exception:
                gql_vars = ""
            if not gql_vars:
                self._set_field_valid(self.body_text, None)
                self._body_valid = True
                return
            if len(gql_vars) > _MAX_SYNC_JSON_VALIDATE_BYTES:
                self._set_field_valid(self.body_text, None)
                self._body_valid = True
                logger.warning(
                    "request_panel.validation.graphql_vars_skip_large op=validate_body size=%d",
                    len(gql_vars),
                )
                return
            try:
                json.loads(gql_vars)
                self._set_field_valid(self.body_text, "valid")
                self._body_valid = True
                logger.debug("GraphQL variables JSON validation passed")
            except json.JSONDecodeError as e:
                msg = f"Invalid GraphQL variables JSON at line {e.lineno}, col {e.colno}: {e.msg}"
                self._set_field_valid(self.body_text, "error", msg)
                self._body_valid = False
                logger.debug("GraphQL variables JSON validation failed: %s", msg)
            return

        # JSON body mode: validate regardless of Content-Type header.
        if not body_text:
            self._set_field_valid(self.body_text, None)
            self._body_valid = True
            return
        if len(body_text) > _MAX_SYNC_JSON_VALIDATE_BYTES:
            self._set_field_valid(self.body_text, None)
            self._body_valid = True
            logger.warning(
                "request_panel.validation.json_skip_large op=validate_body size=%d",
                len(body_text),
            )
            return
        try:
            json.loads(body_text)
            self._set_field_valid(self.body_text, "valid")
            self._body_valid = True
            logger.debug("JSON body validation passed")
        except json.JSONDecodeError as e:
            msg = f"Invalid JSON at line {e.lineno}, col {e.colno}: {e.msg}"
            self._set_field_valid(self.body_text, "error", msg)
            self._body_valid = False
            logger.debug("JSON body validation failed: %s", msg)

    def _set_field_valid(self, field: QWidget, status: str | None, message: str = "") -> None:
        """Display validation status on field with visual feedback.

        Args:
            field: Input widget to style
            status: "valid" (green ✓), "error" (red ✗), None (reset to default)
            message: Tooltip message for error details
        """
        field.setObjectName(
            "field-valid" if status == "valid" else "field-error" if status == "error" else ""
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

        # Disable send if URL is empty or invalid
        if not url_text:
            self.send_button.setEnabled(False)
            return

        if not self._url_valid:
            self.send_button.setEnabled(False)
            return

        # If there are validation errors in headers or body, warn but allow send
        # (server errors are more informative than client lockout)
        if not self._headers_valid or not self._body_valid:
            logger.debug(
                "request_panel.validation.send_gate headers_valid=%s body_valid=%s",
                self._headers_valid,
                self._body_valid,
            )
            # Actually, let's disable if body is invalid (syntactic error)
            if not self._body_valid:
                self.send_button.setEnabled(False)
                return
            # But allow headers issues (server will error anyway)

        # All critical checks passed
        self.send_button.setEnabled(True)
