"""Multipart builder helper for preparing httpx `files` entries.

Centralises file validation and handle management so callers (HTTPClient)
don't duplicate file-opening logic.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def build_multipart_files(
    multipart_data: list[dict[str, Any]] | None,
) -> tuple[list[tuple[str, Any]] | None, list[Any]]:
    """Build the httpx ``files`` list from multipart_data, opening file handles.

    Returns (multipart_files_or_None, opened_file_handles).
    """
    if not multipart_data:
        logger.debug("No multipart data provided")
        return None, []

    multipart_files: list[tuple[str, Any]] = []
    opened_file_handles: list[Any] = []

    try:
        for field in multipart_data:
            field_key = (field.get("key") or "").strip()
            if not field_key:
                continue

            if field.get("type") == "file":
                file_path = (field.get("value") or "").strip()
                if file_path and os.path.isfile(file_path):
                    from equinox.core.validation import Validator

                    resolved = Validator.validate_file_path(file_path)
                    file_handle = open(resolved, "rb")
                    opened_file_handles.append(file_handle)
                    logger.debug("Multipart: added file field %s", field_key)
                    multipart_files.append((field_key, (resolved.name, file_handle)))
                else:
                    logger.warning(
                        "Multipart: file %r not found for field %s, sending empty part",
                        file_path,
                        field_key,
                    )
                    multipart_files.append((field_key, (None, b"")))
            else:
                raw_value = field.get("value")
                if isinstance(raw_value, str):
                    value = raw_value
                elif raw_value is None:
                    value = ""
                else:
                    value = str(raw_value)
                logger.debug("Multipart: added text field %s", field_key)
                multipart_files.append((field_key, (None, value)))
    except Exception:
        # Do not leak already-opened handles when a later field fails
        # validation or open().
        for file_handle in opened_file_handles:
            try:
                file_handle.close()
            except Exception:
                pass
        logger.debug(
            "Multipart: closed %d handle(s) after failure",
            len(opened_file_handles),
        )
        raise

    return multipart_files or None, opened_file_handles
