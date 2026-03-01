"""Audit logging for security events.

This module provides comprehensive audit logging for:
- Authentication attempts
- Credential access
- File operations
- Plugin loading
- Configuration changes
- Security violations
"""

import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class AuditEventType(Enum):
    """Types of audit events."""

    # Authentication
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    AUTH_TOKEN_REFRESH = "auth_token_refresh"

    # Credentials
    CREDENTIAL_STORED = "credential_stored"
    CREDENTIAL_RETRIEVED = "credential_retrieved"
    CREDENTIAL_DELETED = "credential_deleted"
    CREDENTIAL_EXPORT = "credential_export"

    # HTTP Requests
    REQUEST_SENT = "request_sent"
    REQUEST_FAILED = "request_failed"

    # File Operations
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"

    # Plugin System
    PLUGIN_LOADED = "plugin_loaded"
    PLUGIN_UNLOADED = "plugin_unloaded"
    PLUGIN_ERROR = "plugin_error"

    # Security Events
    VALIDATION_FAILURE = "validation_failure"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    SSL_VERIFICATION_FAILED = "ssl_verification_failed"
    INJECTION_ATTEMPT = "injection_attempt"

    # Configuration
    CONFIG_CHANGED = "config_changed"
    SETTINGS_UPDATED = "settings_updated"


class AuditLevel(Enum):
    """Severity levels for audit events."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditLogger:
    """Audit logger for security events.

    Logs security-relevant events to a dedicated audit log file
    with structured JSON format for easy parsing and analysis.
    """

    def __init__(self, log_path: Optional[Path] = None):
        """Initialize audit logger.

        Args:
            log_path: Path to audit log file
        """
        if log_path is None:
            log_path = Path.home() / ".equinox" / "audit.log"

        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        # Configure audit logger
        self.logger = logging.getLogger("equinox.audit")
        self.logger.setLevel(logging.INFO)

        # Prevent propagation to root logger
        self.logger.propagate = False

        # Remove existing handlers
        self.logger.handlers.clear()

        # Add file handler
        handler = logging.FileHandler(self.log_path, encoding="utf-8")
        handler.setLevel(logging.INFO)

        # Use JSON format
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)

        self.logger.addHandler(handler)

    def log_event(
        self,
        event_type: AuditEventType,
        level: AuditLevel = AuditLevel.INFO,
        message: str = "",
        details: Optional[Dict[str, Any]] = None,
        user: Optional[str] = None,
    ) -> None:
        """Log an audit event.

        Args:
            event_type: Type of event
            level: Severity level
            message: Human-readable message
            details: Additional event details (will be sanitized)
            user: User identifier
        """
        # Create audit record
        record = {
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "event_type": event_type.value,
            "level": level.value,
            "message": message,
            "user": user or "system",
            "details": self._sanitize_details(details or {}),
        }

        # Log as JSON
        try:
            log_line = json.dumps(record, ensure_ascii=False)
            self.logger.info(log_line)
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

    def _sanitize_details(self, details: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize details to remove sensitive information.

        Args:
            details: Event details

        Returns:
            Sanitized details
        """
        sanitized = {}

        # List of keys that should be redacted
        sensitive_keys = {
            "password",
            "token",
            "secret",
            "api_key",
            "bearer",
            "authorization",
            "credential",
            "private_key",
        }

        for key, value in details.items():
            key_lower = key.lower()

            # Check if key is sensitive
            if any(sensitive in key_lower for sensitive in sensitive_keys):
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_details(value)
            elif isinstance(value, (list, tuple)):
                sanitized[key] = [
                    self._sanitize_details(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                # Truncate long strings
                if isinstance(value, str) and len(value) > 200:
                    sanitized[key] = value[:200] + "..."
                else:
                    sanitized[key] = value

        return sanitized

    def log_auth_success(self, auth_type: str, user: Optional[str] = None):
        """Log successful authentication."""
        self.log_event(
            AuditEventType.AUTH_SUCCESS,
            AuditLevel.INFO,
            f"Authentication successful: {auth_type}",
            {"auth_type": auth_type},
            user=user,
        )

    def log_auth_failure(self, auth_type: str, reason: str, user: Optional[str] = None):
        """Log failed authentication."""
        self.log_event(
            AuditEventType.AUTH_FAILURE,
            AuditLevel.WARNING,
            f"Authentication failed: {auth_type} - {reason}",
            {"auth_type": auth_type, "reason": reason},
            user=user,
        )

    def log_credential_access(self, operation: str, key: str, user: Optional[str] = None):
        """Log credential access."""
        event_map = {
            "store": AuditEventType.CREDENTIAL_STORED,
            "retrieve": AuditEventType.CREDENTIAL_RETRIEVED,
            "delete": AuditEventType.CREDENTIAL_DELETED,
        }

        self.log_event(
            event_map.get(operation, AuditEventType.CREDENTIAL_RETRIEVED),
            AuditLevel.INFO,
            f"Credential {operation}: {key}",
            {"operation": operation, "credential_key": key},
            user=user,
        )

    def log_request(
        self,
        method: str,
        url: str,
        status_code: Optional[int] = None,
        error: Optional[str] = None,
        user: Optional[str] = None,
    ):
        """Log HTTP request."""
        if error:
            self.log_event(
                AuditEventType.REQUEST_FAILED,
                AuditLevel.ERROR,
                f"{method} {url} failed: {error}",
                {"method": method, "url": url, "error": error},
                user=user,
            )
        else:
            self.log_event(
                AuditEventType.REQUEST_SENT,
                AuditLevel.INFO,
                f"{method} {url} - {status_code}",
                {"method": method, "url": url, "status_code": status_code},
                user=user,
            )

    def log_plugin_event(
        self,
        plugin_name: str,
        action: str,
        error: Optional[str] = None,
        user: Optional[str] = None,
    ):
        """Log plugin event."""
        event_map = {
            "loaded": AuditEventType.PLUGIN_LOADED,
            "unloaded": AuditEventType.PLUGIN_UNLOADED,
            "error": AuditEventType.PLUGIN_ERROR,
        }

        level = AuditLevel.ERROR if error else AuditLevel.INFO

        self.log_event(
            event_map.get(action, AuditEventType.PLUGIN_ERROR),
            level,
            f"Plugin {action}: {plugin_name}",
            {"plugin_name": plugin_name, "action": action, "error": error},
            user=user,
        )

    def log_security_violation(
        self, violation_type: str, details: Dict[str, Any], user: Optional[str] = None
    ):
        """Log security violation."""
        event_map = {
            "validation": AuditEventType.VALIDATION_FAILURE,
            "rate_limit": AuditEventType.RATE_LIMIT_EXCEEDED,
            "ssl": AuditEventType.SSL_VERIFICATION_FAILED,
            "injection": AuditEventType.INJECTION_ATTEMPT,
        }

        self.log_event(
            event_map.get(violation_type, AuditEventType.VALIDATION_FAILURE),
            AuditLevel.WARNING,
            f"Security violation: {violation_type}",
            details,
            user=user,
        )

    def log_file_operation(
        self, operation: str, file_path: str, user: Optional[str] = None
    ):
        """Log file operation."""
        event_map = {
            "read": AuditEventType.FILE_READ,
            "write": AuditEventType.FILE_WRITE,
            "delete": AuditEventType.FILE_DELETE,
        }

        self.log_event(
            event_map.get(operation, AuditEventType.FILE_READ),
            AuditLevel.INFO,
            f"File {operation}: {file_path}",
            {"operation": operation, "file_path": file_path},
            user=user,
        )

    def rotate_log(self, max_size_mb: int = 10) -> None:
        """Rotate audit log if it exceeds size limit.

        Args:
            max_size_mb: Maximum log size in megabytes
        """
        if not self.log_path.exists():
            return

        size_mb = self.log_path.stat().st_size / (1024 * 1024)

        if size_mb > max_size_mb:
            # Rotate log
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            rotated_path = self.log_path.parent / f"audit_{timestamp}.log"

            try:
                self.log_path.rename(rotated_path)
                logger.info(f"Rotated audit log to {rotated_path}")
            except Exception as e:
                logger.error(f"Failed to rotate audit log: {e}")


# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get global audit logger instance.

    Returns:
        AuditLogger instance
    """
    global _audit_logger

    if _audit_logger is None:
        _audit_logger = AuditLogger()

    return _audit_logger
