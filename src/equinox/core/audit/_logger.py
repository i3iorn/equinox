import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from equinox.core.audit._type import AuditEventType
from equinox.core.audit._level import AuditLevel
from equinox.core.util.time import utc_now
from equinox.security import sanitize_details, redact_body, redact_url

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, log_path: Optional[Path] = None):
        if log_path is None:
            log_path = Path.home() / ".equinox" / "audit.log"

        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger("equinox.audit")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        # Close and remove existing handlers to avoid leaked file descriptors
        for h in list(self.logger.handlers):
            try:
                h.close()
            except Exception:
                pass
            self.logger.removeHandler(h)

        handler = logging.FileHandler(self.log_path, encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))
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
            "timestamp": utc_now().isoformat() + "Z",
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
            logger.debug("Audit event logged: type=%s level=%s", event_type.value, level.value)
        except Exception as e:
            logger.error("Failed to write audit log: %s", e)

    def _sanitize_details(self, details: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize details to remove sensitive information.

        Args:
            details: Event details

        Returns:
            Sanitized details
        """
        # Delegate to central redact.sanitize_details to keep behavior
        # consistent across modules (audit, logging, exports).
        return sanitize_details(details, max_string_len=200)

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
        safe_reason = redact_body(reason, max_length=200) or "unknown"
        self.log_event(
            AuditEventType.AUTH_FAILURE,
            AuditLevel.WARNING,
            f"Authentication failed: {auth_type} - {safe_reason}",
            {"auth_type": auth_type, "reason": safe_reason},
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
        safe_url = redact_url(url)
        safe_error = redact_body(error, max_length=200) if error else None
        if error:
            self.log_event(
                AuditEventType.REQUEST_FAILED,
                AuditLevel.ERROR,
                f"{method} {safe_url} failed: {safe_error}",
                {"method": method, "url": safe_url, "error": safe_error},
                user=user,
            )
        else:
            self.log_event(
                AuditEventType.REQUEST_SENT,
                AuditLevel.INFO,
                f"{method} {safe_url} - {status_code}",
                {"method": method, "url": safe_url, "status_code": status_code},
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

        try:
            size_mb = self.log_path.stat().st_size / (1024 * 1024)
        except OSError:
            return

        if size_mb > max_size_mb:
            # Rotate log
            timestamp = utc_now().strftime("%Y%m%d_%H%M%S")
            rotated_path = self.log_path.parent / f"audit_{timestamp}.log"

            try:
                self.log_path.rename(rotated_path)
                logger.info("Rotated audit log to %s", rotated_path)
            except OSError:
                # On Windows the file may be locked by another handler;
                # fall back to copy-and-truncate.
                try:
                    import shutil
                    shutil.copy2(self.log_path, rotated_path)
                    with open(self.log_path, "w") as f:
                        f.truncate(0)
                    logger.info("Rotated audit log to %s (copy+truncate)", rotated_path)
                except Exception as e:
                    logger.error("Failed to rotate audit log: %s", type(e).__name__)
