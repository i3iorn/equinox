"""Real-time validation for request fields in RequestPanel.

Provides inline validation feedback as users type in URL, headers, and body fields.
Shows validation status with visual indicators and prevents send if critical errors exist.
"""

import json
import logging
from typing import Optional

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import QTimer

from equinox.core.validation import Validator
from equinox.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


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
            self._url_valid = True
            return

        # Skip validation if URL contains unresolved variables
        if "{{" in url_text and "}}" in url_text:
            self._set_field_valid(self.url_input, None)  # Don't validate templates
            self._url_valid = True
            return

        try:
            Validator.validate_resolved_url(url_text)
            self._set_field_valid(self.url_input, "valid")
            self._url_valid = True
            logger.debug("URL validation passed: %s", url_text[:50])
        except ValidationError as e:
            self._set_field_valid(self.url_input, "error", str(e))
            self._url_valid = False
            logger.debug("URL validation failed: %s", str(e))

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

        # Empty is OK
        if not body_text:
            self._set_field_valid(self.body_text, None)
            self._body_valid = True
            return

        # Check if body format is JSON (from body type picker)
        body_format = getattr(self, 'body_format', None) or "text"
        if body_format != "json":
            self._set_field_valid(self.body_text, None)  # Don't validate non-JSON
            self._body_valid = True
            return

        # Check Content-Type header to see if this should be JSON
        headers = self.headers_table.get_data() or {}
        content_type = headers.get("Content-Type", "").lower()
        is_json_content = "json" in content_type

        if not is_json_content and "{{" not in body_text:
            # Not JSON content-type and no template, so don't validate as JSON
            self._set_field_valid(self.body_text, None)
            self._body_valid = True
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

    def _set_field_valid(self, field: QWidget, status: Optional[str], message: str = "") -> None:
        """Display validation status on field with visual feedback.

        Args:
            field: Input widget to style
            status: "valid" (green ✓), "error" (red ✗), None (reset to default)
            message: Tooltip message for error details
        """
        field.setObjectName("field-valid" if status == "valid" else "field-error" if status == "error" else "")
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
            logger.warning(
                "Send button would be disabled: headers_valid=%s, body_valid=%s",
                self._headers_valid, self._body_valid
            )
            # Actually, let's disable if body is invalid (syntactic error)
            if not self._body_valid:
                self.send_button.setEnabled(False)
                return
            # But allow headers issues (server will error anyway)

        # All critical checks passed
        self.send_button.setEnabled(True)

