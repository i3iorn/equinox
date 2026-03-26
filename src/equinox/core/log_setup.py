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
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any


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


# ──────────────────────────────────────────────────────────────────────────────
# JSON formatter
# ──────────────────────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    EXTRA_FIELDS = {
        "event", "method", "url", "headers", "params", "timeout", "verify_ssl",
        "status", "status_code", "reason", "elapsed_time_seconds", "elapsed_ms",
        "size_bytes", "error_type", "error_message", "request_id", "timestamp",
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

        # Process/thread info
        if record.processName != "MainProcess":
            doc["process"] = record.processName
        if record.threadName != "MainThread":
            doc["thread"] = record.threadName

        # Structured extras
        for field in self.EXTRA_FIELDS:
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

        return json.dumps(doc, ensure_ascii=False, default=str)


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

        if self.supports_colour:
            colour = self.COLOURS.get(record.levelname, "")
            line = f"{colour}{ts} {lvl:<5}{self.RESET} [{name}] {msg}"
        else:
            line = f"{ts} {lvl:<5} [{name}] {msg}"

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def configure_logging(
    log_dir: Optional[Path] = None,
    level: int = logging.DEBUG,
    console_level: int = logging.WARNING,
) -> Path:
    """Configure application-wide logging."""
    global _app_corr_id
    _app_corr_id = uuid.uuid4().hex[:12]

    log_dir = log_dir or (Path.home() / ".equinox" / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "equinox.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
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