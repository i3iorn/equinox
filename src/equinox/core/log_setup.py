"""Structured logging setup for Equinox.

Call ``configure_logging()`` once at application startup.  After that every
``logging.getLogger(name)`` call produces records that:

* Are written to ``~/.equinox/logs/equinox.log`` as newline-delimited JSON
  (one record per line — easy to ``grep``, ingest into Splunk/ELK, etc.)
* Are also printed to *stderr* in a compact human-readable format.

Log file is rotated at **10 MB** and up to **5** old files are kept.

Example log line::

    {"ts":"2026-02-20T14:23:01.234Z","level":"INFO","logger":"equinox.gui.request_panel",
     "msg":"GET https://api.example.com/users","elapsed_ms":142,"status":200}
"""

import json
import logging
import logging.handlers
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── JSON formatter ────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    # Fields copied verbatim from the LogRecord if present as ``extra`` kwargs
    _EXTRA_FIELDS = (
        "method", "url", "status", "elapsed_ms",
        "size_bytes", "error_type", "request_id",
    )

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        doc: dict = {
            "ts":     datetime.fromtimestamp(record.created, tz=timezone.utc)
                      .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }

        # Copy structured fields attached via ``extra=``
        for field in self._EXTRA_FIELDS:
            val = getattr(record, field, None)
            if val is not None:
                doc[field] = val

        # Attach exception info if present
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)

        return json.dumps(doc, ensure_ascii=False, default=str)


# ── Human-readable console formatter ─────────────────────────────────────────

class _ConsoleFormatter(logging.Formatter):
    _COLOURS = {
        "DEBUG":    "\033[37m",    # grey
        "INFO":     "\033[32m",    # green
        "WARNING":  "\033[33m",    # yellow
        "ERROR":    "\033[31m",    # red
        "CRITICAL": "\033[35m",    # magenta
    }
    _RESET = "\033[0m"
    _supports_colour = sys.stderr.isatty() if hasattr(sys.stderr, "isatty") else False

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        ts   = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        lvl  = record.levelname[:5]
        name = record.name.split(".")[-1]    # last component only
        msg  = record.getMessage()

        if self._supports_colour:
            colour = self._COLOURS.get(record.levelname, "")
            line = f"{colour}{ts} {lvl:<5}{self._RESET} [{name}] {msg}"
        else:
            line = f"{ts} {lvl:<5} [{name}] {msg}"

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line


# ── Public API ────────────────────────────────────────────────────────────────

def configure_logging(
    log_dir: Optional[Path] = None,
    level: int = logging.DEBUG,
    console_level: int = logging.WARNING,
) -> Path:
    """Configure application-wide logging.

    Args:
        log_dir:       Directory for log files.  Defaults to ``~/.equinox/logs``.
        level:         Minimum level written to the log *file*.
        console_level: Minimum level printed to *stderr*.

    Returns:
        Path to the active log file.
    """
    if log_dir is None:
        log_dir = Path.home() / ".equinox" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "equinox.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)   # handlers filter individually

    # Avoid duplicate handlers when called more than once (e.g. in tests)
    root.handlers.clear()

    # ── Rotating file handler (JSON) ──────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,   # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(_JsonFormatter())
    root.addHandler(file_handler)

    # ── Stderr console handler (human-readable) ───────────────────────
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(_ConsoleFormatter())
    root.addHandler(console_handler)

    # Silence very chatty third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Logging initialised — writing to %s", log_file)

    return log_file


def get_log_file() -> Optional[Path]:
    """Return the path of the current log file, or *None* if not yet configured."""
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            return Path(handler.baseFilename)
    return None

