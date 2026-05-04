"""Audit logging for security events.

This module provides comprehensive audit logging for:
- Authentication attempts
- Credential access
- File operations
- Plugin loading
- Configuration changes
- Security violations
"""

import threading
from typing import Optional

from equinox.core.audit._level import AuditLevel
from equinox.core.audit._logger import AuditLogger
from equinox.core.audit._type import AuditEventType

# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None
_audit_logger_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    """Get or create the global audit logger instance (thread-safe)."""
    global _audit_logger
    if _audit_logger is None:
        with _audit_logger_lock:
            if _audit_logger is None:
                _audit_logger = AuditLogger()
    return _audit_logger

__all__ = ["AuditEventType", "AuditLevel", "get_audit_logger"]