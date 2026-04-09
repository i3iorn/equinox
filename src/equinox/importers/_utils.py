"""Shared utilities for importers.

Contains helpers that are common across all importer classes (OpenAPI, Postman,
HAR, Insomnia) to avoid code duplication.
"""

import logging
from pathlib import Path
from typing import Optional, Sequence

from equinox.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


def validate_import_file(
    path: Path,
    max_bytes: int,
    *,
    allowed_extensions: Optional[Sequence[str]] = None,
    label: str = "Import file",
) -> None:
    """Validate that an import file exists, has an allowed extension, and is not too large.

    Args:
        path:               Path to the file to validate.
        max_bytes:          Maximum permitted file size in bytes.
        allowed_extensions: If given, the file suffix (lowercased) must be one of these
                            values (e.g. ``[".json"]`` or ``[".json", ".yaml", ".yml"]``).
                            Pass ``None`` or an empty sequence to skip the extension check.
        label:              Human-readable name used in error messages (default ``"Import file"``).

    Raises:
        ValidationError: If the file does not exist, has a disallowed extension,
                         or exceeds *max_bytes*.
    """
    if not path.exists():
        raise ValidationError(f"{label} not found: {path}")

    if allowed_extensions:
        suffix = path.suffix.lower()
        if suffix not in allowed_extensions:
            exts = ", ".join(allowed_extensions)
            raise ValidationError(
                f"{label} has unsupported extension '{suffix}'. "
                f"Allowed: {exts}"
            )

    size = path.stat().st_size
    if size > max_bytes:
        raise ValidationError(
            f"{label} too large: {size:,} bytes (max {max_bytes:,} bytes)"
        )
    logger.debug("validate_import_file: OK path=%s size=%d", path, size)

