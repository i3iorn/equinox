"""Multipart builder helper for preparing httpx `files` entries.

Centralises file validation and handle management so callers (HTTPClient)
don't duplicate file-opening logic.
"""

import os
import logging
from pathlib import Path
from typing import Any, List, Tuple, Optional

logger = logging.getLogger(__name__)


def build_multipart_files(multipart_data) -> Tuple[Optional[List[Tuple[str, Any]]], List[Any]]:
    """Build the httpx ``files`` list from multipart_data, opening file handles.

    Returns (multipart_files_or_None, opened_file_handles).
    """
    if not multipart_data:
        logger.debug("No multipart data provided")
        return None, []

    multipart_files: List[Tuple[str, Any]] = []
    opened_file_handles: List[Any] = []

    for field in multipart_data:
        field_key = (field.get("key") or "").strip()
        if not field_key:
            continue

        if field.get("type") == "file":
            file_path = (field.get("value") or "").strip()
            if file_path and os.path.isfile(file_path):
                from equinox.core.validation import Validator
                Validator.validate_file_path(file_path)
                file_handle = open(file_path, "rb")
                opened_file_handles.append(file_handle)
                logger.debug("Multipart: added file field %s = %s", field_key, Path(file_path).name)
                multipart_files.append((field_key, (Path(file_path).name, file_handle)))
            else:
                logger.debug("Multipart: file not found for field %s, sending empty", field_key)
                multipart_files.append((field_key, (None, b"")))
        else:
            value = field.get("value", "")
            value_preview = (value[:30] + "...") if len(value) > 30 else value
            logger.debug("Multipart: added text field %s = %s", field_key, value_preview)
            multipart_files.append((field_key, (None, value)))

    return multipart_files or None, opened_file_handles

