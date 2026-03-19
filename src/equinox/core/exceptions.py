"""Exceptions for Equinox"""
from typing import Optional


class EquinoxError(Exception):
    """Base exception for Equinox.

    All Equinox exceptions inherit from this base class.
    """

    def __init__(self, message: str, details: Optional[dict] = None):
        """Initialize exception.

        Args:
            message: Human-readable error message
            details: Optional dict with additional error context (never shown to user)
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        """Return user-friendly error message."""
        return self.message


class RequestError(EquinoxError):
    """Error during HTTP request execution."""
    pass


class AuthError(EquinoxError):
    """Authentication or authorization error."""
    pass


class StorageError(EquinoxError):
    """Storage/database error."""
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
