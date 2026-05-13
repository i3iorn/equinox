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
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

# ──────────────────────────────────────────────────────────────────────────────
# Module constants
# ──────────────────────────────────────────────────────────────────────────────

# Correlation ID
_CORR_ID_HEX_LENGTH: int = 12

# Log file configuration
_LOG_FILE_NAME: str = "equinox.log"
_LOG_MAX_BYTES: int = 10 * 1024 * 1024   # 10 MB
_LOG_BACKUP_COUNT: int = 5
_LOG_ENCODING: str = "utf-8"

# Safety cap — prevents a single log line from consuming excessive disk / memory.
MAX_LOG_PAYLOAD_SIZE: int = 8192  # 8 KB per serialised JSON log line

# When a field value is truncated, we append this marker so consumers know.
_FIELD_TRUNCATION_MARKER: str = "...[truncated]"

# Maximum bytes allowed in a single string field before it is shortened.
# Keeps each field readable while still fitting many fields in one log line.
_MAX_FIELD_VALUE_LEN: int = 512

# Minimum fields that must survive the final safety-net slim-down.
_SLIM_FIELDS: tuple = ("ts", "level", "logger", "msg")

# Environment variable names for level overrides
_ENV_FILE_LOG_LEVEL: str = "EQUINOX_LOG_LEVEL"
_ENV_CONSOLE_LOG_LEVEL: str = "EQUINOX_CONSOLE_LOG_LEVEL"

# Third-party loggers whose verbosity should be reduced to WARNING.
_NOISY_LOGGERS: tuple = ("httpx", "httpcore", "urllib3", "charset_normalizer")

# ──────────────────────────────────────────────────────────────────────────────
# Application correlation ID state
# ──────────────────────────────────────────────────────────────────────────────


class _AppCorrelationIdProvider:
    """Thread-safe provider for the process-level application correlation ID."""

    def __init__(self) -> None:
        self._value: Optional[str] = None
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if self._value is None:
                self._value = uuid.uuid4().hex[:_CORR_ID_HEX_LENGTH]
            return self._value

    def reset(self) -> str:
        with self._lock:
            self._value = uuid.uuid4().hex[:_CORR_ID_HEX_LENGTH]
            return self._value


_APP_CORR_PROVIDER = _AppCorrelationIdProvider()


def get_app_corr_id() -> str:
    """Return the application correlation ID, generating one if needed."""
    return _APP_CORR_PROVIDER.get()


def reset_app_corr_id() -> str:
    """Regenerate and return a new application-level correlation ID."""
    return _APP_CORR_PROVIDER.reset()


def generate_request_id() -> str:
    """Generate a short unique ID for correlating log entries within a single request."""
    return uuid.uuid4().hex[:_CORR_ID_HEX_LENGTH]


# ──────────────────────────────────────────────────────────────────────────────
# Timestamp helpers
# ──────────────────────────────────────────────────────────────────────────────

def _format_utc_timestamp(ts: float) -> str:
    """Format an epoch timestamp as UTC ISO 8601 with millisecond precision.

    Example: ``"2026-04-09T14:22:01.234Z"``

    Args:
        ts: Epoch time in seconds (e.g. from ``time.time()``).
    """
    # [:-3] drops the last 3 microsecond digits, leaving milliseconds.
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _format_local_timestamp(ts: float) -> str:
    """Format an epoch timestamp as local-time HH:MM:SS.mmm for console display.

    Example: ``"14:22:01.234"``

    Args:
        ts: Epoch time in seconds.
    """
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]


# ──────────────────────────────────────────────────────────────────────────────
# Serialisation helper
# ──────────────────────────────────────────────────────────────────────────────

def _safe_serialize(doc: Dict[str, Any]) -> str:
    """Serialize *doc* to a JSON string that fits within MAX_LOG_PAYLOAD_SIZE.

    Unlike raw string slicing, this approach truncates at the *data* level
    before serialization so the output is always valid JSON.  The sequence is:

    1. Try a direct ``json.dumps`` — fast path for the common case.
    2. If over budget, shorten long string fields and re-serialize.
    3. If still over budget (many fields or non-string blobs), keep only the
       mandatory slim-down fields so the line is never silently dropped.

    In all truncation cases ``_truncated = true`` is added to the document.

    Args:
        doc: Dictionary to serialize.

    Returns:
        A valid JSON string that fits within MAX_LOG_PAYLOAD_SIZE bytes.
    """
    result = json.dumps(doc, ensure_ascii=True, default=str)
    if len(result) <= MAX_LOG_PAYLOAD_SIZE:
        return result

    # --- Step 2: shorten long string field values and re-serialize ----------
    reduced: Dict[str, Any] = {}
    for k, v in doc.items():
        if isinstance(v, str) and len(v) > _MAX_FIELD_VALUE_LEN:
            reduced[k] = v[:_MAX_FIELD_VALUE_LEN] + _FIELD_TRUNCATION_MARKER
        else:
            reduced[k] = v
    reduced["_truncated"] = True
    result = json.dumps(reduced, ensure_ascii=True, default=str)

    if len(result) <= MAX_LOG_PAYLOAD_SIZE:
        return result

    # --- Step 3: slim-down safety net — keep only the most critical fields --
    slim: Dict[str, Any] = {k: reduced[k] for k in _SLIM_FIELDS if k in reduced}
    slim["msg"] = str(slim.get("msg", ""))[:200]
    slim["_truncated"] = True
    return json.dumps(slim, ensure_ascii=True, default=str)


# ──────────────────────────────────────────────────────────────────────────────
# JSON formatter
# ──────────────────────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    EXTRA_FIELDS = {
        "event", "method", "url", "headers", "params", "timeout", "verify_ssl",
        "status", "status_code", "reason", "elapsed_time_seconds", "elapsed_ms",
        "size_bytes", "error_type", "error_message", "request_id", "timestamp",
        "collection_id", "environment_id", "auth_type",
    }

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        return _safe_serialize(self._build_log_doc(record))

    def _build_log_doc(self, record: logging.LogRecord) -> Dict[str, Any]:
        """Construct the structured log dictionary from *record*.

        Populates base fields, optional request/process/thread ids, all known
        EXTRA_FIELDS present on the record, a freeform payload dict, and
        exception info when present.
        """
        doc: Dict[str, Any] = {
            "ts":           _format_utc_timestamp(record.created),
            "app_corr_id":  get_app_corr_id(),
            "level":        record.levelname,
            "logger":       record.name,
            "msg":          record.getMessage(),
        }

        # Per-request correlation id
        req_id = getattr(record, "request_id", None)
        if req_id:
            doc["request_id"] = req_id

        # Process/thread info — omit in the common single-process/thread case
        if record.processName != "MainProcess":
            doc["process"] = record.processName
        if record.threadName != "MainThread":
            doc["thread"] = record.threadName

        # Structured extras from known fields
        for field in self.EXTRA_FIELDS:
            if field == "request_id":
                continue  # already handled above
            value = getattr(record, field, None)
            if value is not None:
                doc[field] = value

        # Merge freeform payload dict (non-destructive: existing keys win)
        payload = getattr(record, "payload", None)
        if isinstance(payload, dict):
            for k, v in payload.items():
                doc.setdefault(k, v)

        # Exception info
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)

        return doc


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
        ts = _format_local_timestamp(record.created)
        lvl = record.levelname[:5]
        name = record.name.rsplit(".", 1)[-1]
        msg = record.getMessage()

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
    "DEBUG":    logging.DEBUG,
    "INFO":     logging.INFO,
    "WARN":     logging.WARNING,   # common alias
    "WARNING":  logging.WARNING,
    "ERROR":    logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL":    logging.CRITICAL,  # common alias
}


def _resolve_level(env_var: str, default: int) -> int:
    """Return a logging level from an environment variable, falling back to *default*."""
    raw = os.environ.get(env_var, "").upper().strip()
    return _LEVEL_NAMES.get(raw, default)


# ──────────────────────────────────────────────────────────────────────────────
# configure_logging helpers
# ──────────────────────────────────────────────────────────────────────────────

def _reset_root_logger() -> logging.Logger:
    """Remove all existing handlers from the root logger and set level to DEBUG.

    Closes each handler before removing it to prevent file-descriptor leaks.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        try:
            handler.close()
        except Exception:
            pass
    root.handlers.clear()
    return root


def _make_file_handler(log_file: Path, level: int) -> logging.handlers.RotatingFileHandler:
    """Build a rotating file handler that writes newline-delimited JSON.

    Args:
        log_file: Absolute path to the log file.
        level:    Minimum log level for this handler.
    """
    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding=_LOG_ENCODING,
    )
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    return handler


def _make_console_handler(level: int) -> logging.StreamHandler:
    """Build a stderr console handler with human-readable output.

    Args:
        level: Minimum log level for this handler.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(ConsoleFormatter())
    return handler


def _silence_noisy_loggers() -> None:
    """Raise the log level of known verbose third-party libraries to WARNING."""
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


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

    Args:
        log_dir:       Directory for the rotating log file.
                       Defaults to ``~/.equinox/logs``.
        level:         Minimum level written to the log file.
        console_level: Minimum level printed to stderr.

    Returns:
        Path to the active log file.
    """
    reset_app_corr_id()

    level = _resolve_level(_ENV_FILE_LOG_LEVEL, level)
    console_level = _resolve_level(_ENV_CONSOLE_LOG_LEVEL, console_level)

    log_dir = log_dir or (Path.home() / ".equinox" / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / _LOG_FILE_NAME

    root = _reset_root_logger()
    root.addHandler(_make_file_handler(log_file, level))
    root.addHandler(_make_console_handler(console_level))
    _silence_noisy_loggers()

    logging.getLogger(__name__).info(
        "Logging initialised — app_corr_id=%s writing to %s",
        get_app_corr_id(), log_file,
    )

    return log_file


def get_log_file() -> Optional[Path]:
    """Return the path of the current log file, or None if not configured."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            return Path(handler.baseFilename)
    return None

