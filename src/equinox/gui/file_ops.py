"""GUI-safe filesystem helpers for user-selected paths."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def validate_selected_path(
    raw_path: str,
    *,
    must_exist: bool,
    allow_directory: bool = False,
) -> Path:
    """Validate a path selected from a GUI file dialog.

    Raises ``ValueError`` when the path is invalid or does not meet existence
    constraints.
    """
    candidate = (raw_path or "").strip()
    if not candidate or "\x00" in candidate:
        raise ValueError("Selected path is empty or invalid.")

    try:
        path = Path(candidate).expanduser()
    except (TypeError, ValueError) as exc:
        raise ValueError("Selected path is invalid.") from exc

    if must_exist and not path.exists():
        raise ValueError("Selected path does not exist.")

    if must_exist:
        if path.is_dir() and not allow_directory:
            raise ValueError("Selected path must be a file.")
    else:
        parent = path.parent
        if not parent.exists() or not parent.is_dir():
            raise ValueError("Destination folder does not exist.")
        if path.exists() and path.is_dir() and not allow_directory:
            raise ValueError("Destination path must be a file.")

    return path


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write bytes atomically to ``path`` to avoid partial files on interruption."""
    if not isinstance(payload, (bytes, bytearray)):
        raise ValueError("Payload must be bytes.")

    tmp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as tmp:
            tmp.write(bytes(payload))
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(str(tmp_path), str(path))
    except Exception:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def safe_read_text_file(
    path: Path,
    *,
    max_bytes: int,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> str:
    """Read UTF text from ``path`` with an explicit size cap."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero.")
    if not path.exists() or not path.is_file():
        raise ValueError("Selected path does not exist or is not a file.")

    size_bytes = path.stat().st_size
    if size_bytes > max_bytes:
        raise ValueError(
            f"File is too large ({size_bytes} bytes). Maximum allowed is {max_bytes} bytes."
        )

    return path.read_text(encoding=encoding, errors=errors)
