"""
Structured logging setup for Equinox.

Call `configure_logging()` once at application startup. After that every
`logging.getLogger(name)` call produces records that:

* Are written to `~/.equinox/logs/equinox.log` as newline-delimited JSON.
* Are also printed to stderr in a compact human-readable format.

Log file is rotated at 10 MB and up to 5 old files are kept.
"""

import json
import logging
import logging.handlers
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Protocol

# ──────────────────────────────────────────────────────────────────────────────
# Global application correlation ID
# ──────────────────────────────────────────────────────────────────────────────

_app_corr_id: Optional[str] = None


def get_app_corr_id() -> str:
    """Return the application correlation ID, generating one if needed."""
    global _app_corr_id
    if _app_corr_id is None:
        _app_corr_id = uuid.uuid4().hex[:12]
    return _app_corr_id


def generate_request_id() -> str:
    """Generate a short unique ID for correlating log entries within a single request."""
    return uuid.uuid4().hex[:12]


# ──────────────────────────────────────────────────────────────────────────────
# JSON formatter
# ──────────────────────────────────────────────────────────────────────────────

# Safety cap — prevents a single log line from consuming excessive disk / memory.
MAX_LOG_PAYLOAD_SIZE = 8192  # 8 KB per serialised JSON log line


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    EXTRA_FIELDS = {
        "event", "method", "url", "headers", "params", "timeout", "verify_ssl",
        "status", "status_code", "reason", "elapsed_time_seconds", "elapsed_ms",
        "size_bytes", "error_type", "error_message", "request_id", "timestamp",
        "collection_id", "environment_id", "auth_type",
    }

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        doc: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
                  .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "app_corr_id": get_app_corr_id(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Per-request correlation id
        req_id = getattr(record, "request_id", None)
        if req_id:
            doc["request_id"] = req_id

        # Process/thread info
        if record.processName != "MainProcess":
            doc["process"] = record.processName
        if record.threadName != "MainThread":
            doc["thread"] = record.threadName

        # Structured extras
        for field in self.EXTRA_FIELDS:
            if field == "request_id":
                continue  # already handled above
            if hasattr(record, field):
                value = getattr(record, field)
                if value is not None:
                    doc[field] = value

        # Merge payload dict
        payload = getattr(record, "payload", None)
        if isinstance(payload, dict):
            for k, v in payload.items():
                doc.setdefault(k, v)

        # Exception info
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)

        result = json.dumps(doc, ensure_ascii=False, default=str)

        # Safety cap — truncate excessively large log lines
        if len(result) > MAX_LOG_PAYLOAD_SIZE:
            result = result[:MAX_LOG_PAYLOAD_SIZE - 20] + ',"_truncated":true}'

        return result


# ──────────────────────────────────────────────────────────────────────────────
# Console formatter
# ──────────────────────────────────────────────────────────────────────────────

class ConsoleFormatter(logging.Formatter):
    COLOURS = {
        "DEBUG":    "\033[37m",
        "INFO":     "\033[32m",
        "WARNING":  "\033[33m",
        "ERROR":    "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def __init__(self) -> None:
        super().__init__()
        self.supports_colour = getattr(sys.stderr, "isatty", lambda: False)()

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        lvl = record.levelname[:5]
        name = record.name.rsplit(".", 1)[-1]
        msg = record.getMessage()

        # Include per-request correlation id when available
        req_id = getattr(record, "request_id", None)
        rid_tag = f" [{req_id}]" if req_id else ""

        if self.supports_colour:
            colour = self.COLOURS.get(record.levelname, "")
            line = f"{colour}{ts} {lvl:<5}{self.RESET} [{name}]{rid_tag} {msg}"
        else:
            line = f"{ts} {lvl:<5} [{name}]{rid_tag} {msg}"

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line


# ──────────────────────────────────────────────────────────────────────────────
# Log level resolver
# ──────────────────────────────────────────────────────────────────────────────

_LEVEL_NAMES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _resolve_level(env_var: str, default: int) -> int:
    """Return a logging level from an environment variable, falling back to *default*."""
    raw = os.environ.get(env_var, "").upper().strip()
    return _LEVEL_NAMES.get(raw, default)


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def configure_logging(
    log_dir: Optional[Path] = None,
    level: int = logging.DEBUG,
    console_level: int = logging.WARNING,
) -> Path:
    """Configure application-wide logging.

    Log levels can be overridden via environment variables:
    * ``EQUINOX_LOG_LEVEL``         — file log level (default DEBUG)
    * ``EQUINOX_CONSOLE_LOG_LEVEL`` — stderr log level (default WARNING)
    """
    global _app_corr_id
    _app_corr_id = uuid.uuid4().hex[:12]

    # Allow environment variable overrides
    level = _resolve_level("EQUINOX_LOG_LEVEL", level)
    console_level = _resolve_level("EQUINOX_CONSOLE_LOG_LEVEL", console_level)

    log_dir = log_dir or (Path.home() / ".equinox" / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "equinox.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Close existing handlers before removing them to avoid file-descriptor leaks.
    for _h in list(root.handlers):
        try:
            _h.close()
        except Exception:
            pass
    root.handlers.clear()

    # File handler
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(ConsoleFormatter())
    root.addHandler(console_handler)

    # Quiet noisy libs
    for noisy in ("httpx", "httpcore", "urllib3", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised — app_corr_id=%s writing to %s",
        _app_corr_id, log_file
    )

    return log_file


def get_log_file() -> Optional[Path]:
    """Return the path of the current log file, or None if not configured."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            return Path(handler.baseFilename)
    return None


class AuditLoggerLike(Protocol):
    """Structural interface required from the optional audit logger.

    Only the single method called by RateLimiter is declared here. This keeps
    the dependency lightweight and avoids importing the concrete AuditLogger,
    which prevents circular imports.
    """

    def log_security_violation(
        self,
        violation_type: str,
        details: dict,
        user: Optional[str] = None,
    ) -> None: ...
