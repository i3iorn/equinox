"""Shared GUI helpers for opening the active Equinox log file."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from equinox.core.log_setup import get_log_file


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
    log_path: Optional[Path] = None
    resolved_path: Optional[Path] = None
    error: Optional[str] = None


def open_path_in_os(path: Path) -> None:
    """Open *path* using the platform default application."""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603


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

