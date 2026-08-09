"""Shared GUI helpers for opening the active Equinox log file."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtWidgets import QWidget

from equinox.core.log_setup import get_log_file
from equinox.gui.error_presenter import ErrorPresenter

logger = logging.getLogger(__name__)


class LogOpenStatus:
    """Result status values for log-file open attempts."""

    OPENED = "opened"
    MISSING = "missing"
    INVALID_PATH = "invalid_path"
    OPEN_FAILED = "open_failed"


@dataclass
class LogOpenResult:
    """Outcome of attempting to open the active structured log file."""

    status: str
    log_path: Path | None = None
    resolved_path: Path | None = None
    error: str | None = None


def open_path_in_os(path: Path) -> None:
    """Open *path* using the platform default application."""
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def try_open_current_log_file() -> LogOpenResult:
    """Validate and open the current Equinox log file."""
    log_path = get_log_file()
    if not log_path or not log_path.exists():
        return LogOpenResult(status=LogOpenStatus.MISSING, log_path=log_path)

    resolved = log_path.resolve()
    if resolved.suffix.lower() != ".log":
        return LogOpenResult(
            status=LogOpenStatus.INVALID_PATH,
            log_path=log_path,
            resolved_path=resolved,
        )

    try:
        open_path_in_os(resolved)
    except Exception as exc:  # pragma: no cover - platform-specific path opener
        return LogOpenResult(
            status=LogOpenStatus.OPEN_FAILED,
            log_path=log_path,
            resolved_path=resolved,
            error=str(exc),
        )

    return LogOpenResult(
        status=LogOpenStatus.OPENED,
        log_path=log_path,
        resolved_path=resolved,
    )


def show_log_file_open_result(
    parent: QWidget | None,
    result: LogOpenResult,
    missing_message: str,
) -> bool:
    """Display GUI feedback for a log-file open attempt.

    Returns ``True`` when the file was opened successfully, ``False`` otherwise.
    """
    if result.status == LogOpenStatus.MISSING:
        ErrorPresenter.info(parent, missing_message, title="Log File")
        return False

    if result.status == LogOpenStatus.INVALID_PATH:
        logger.warning("Refusing to open non-log file: %s", result.resolved_path)
        ErrorPresenter.info(
            parent,
            f"Refusing to open unexpected file:\n{result.resolved_path}\n\n"
            "The configured log path does not end in '.log'.",
            title="Log File",
        )
        return False

    if result.status == LogOpenStatus.OPEN_FAILED:
        ErrorPresenter.info(
            parent,
            f"Log file:\n{result.log_path}\n\n(Could not open automatically: {result.error})",
            title="Log File",
        )
        return False

    return True
