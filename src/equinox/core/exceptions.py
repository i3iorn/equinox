"""Exceptions for Equinox"""

from typing import Any


class EquinoxError(Exception):
    """Base exception for Equinox.

    All Equinox exceptions inherit from this base class.
    All Equinox exceptions inherit from this base class.
    """

    # Hint templates for common errors — provides actionable suggestions
    HINTS = {
        "url_scheme": "Did you include the scheme? Use 'http://' or 'https://'.",
        "url_too_long": "URL is too long (max 2048 characters). Shorten query string or use POST body.",
        "timeout": "Try increasing the timeout or checking if the server is reachable.",
        "ssl_verify": "If using a self-signed certificate, disable verification (⚠️ security risk).",
        "auth_failed": "Check if credentials are correct and not expired.",
        "rate_limit": "You've hit the API rate limit. Equinox will auto-retry; or wait before retrying.",
        "connection": "Check network connectivity. Is the server reachable? Try: ping example.com",
        "empty_response": "Server returned empty response. Check if the endpoint exists.",
        "invalid_json": "Request body is not valid JSON. Check for missing quotes, trailing commas, etc.",
        "header_size": "Total header size exceeds limit (16 KB). Remove some headers.",
        "body_size": "Request body exceeds size limit (100 MB). Split into chunks or use CDN.",
    }

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
        hint_key: str | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            message: Human-readable error message
            details: Optional dict with additional error context (never shown to user)
            hint_key: Optional key to hint dictionary for actionable suggestion
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.hint_key = hint_key

    def __str__(self) -> str:
        """Return user-friendly error message."""
        return self.message

    def user_facing_message(self) -> str:
        """Return message + hint for user display.

        Combines the error message with an actionable hint if one is available.
        Used in GUI to show helpful guidance to users.
        """
        msg = self.message
        if self.hint_key and self.hint_key in self.HINTS:
            msg += f"\n\n💡 {self.HINTS[self.hint_key]}"
        return msg


class RequestError(EquinoxError):
    """Error during HTTP request execution."""

    pass


class AuthError(EquinoxError):
    """Authentication or authorization error."""

    pass


class CredentialValidationError(AuthError):
    """Raised when credential validation fails.

    Attributes:
        field_name: Name of the field that failed validation.
        reason: Specific reason for the validation failure.
    """

    def __init__(self, field_name: str, reason: str):
        self.field_name = field_name
        self.reason = reason
        super().__init__(f"{field_name}: {reason}")


class StorageError(EquinoxError):
    """Storage/database error."""

    pass


class DuplicateError(StorageError):
    """Raised when a unique constraint or duplicate-key violation occurs in storage.

    Subclasses :class:`StorageError` so existing code that catches StorageError
    remains compatible, but callers can catch DuplicateError for more granular
    handling.
    """

    pass


class PluginError(EquinoxError):
    """Plugin loading or execution error."""

    pass


class ValidationError(EquinoxError):
    """Input validation error.

    Raised when user input fails validation checks.
    """

    pass


class SecurityError(EquinoxError):
    """Security-related error.

    Raised when a security violation is detected.
    """

    pass


class RateLimitError(EquinoxError):
    """Rate limit exceeded error."""

    pass


class RequestTimeoutError(EquinoxError):
    """Request timeout error."""

    pass


# Backward-compatible alias — prefer ``RequestTimeoutError`` in new code.
TimeoutError = RequestTimeoutError


class FileSizeError(EquinoxError):
    """File size limit exceeded."""

    pass


class CertificateError(EquinoxError):
    """SSL/TLS certificate validation error."""

    pass


class JsonParseError(EquinoxError):
    """JSON parsing error."""

    pass


class JsonTypeError(EquinoxError):
    """JSON type error."""

    pass
