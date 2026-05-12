"""Shared utilities for importers.

Contains helpers that are common across all importer classes (OpenAPI, Postman,
HAR, Insomnia) to avoid code duplication.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from equinox.core import urls
from equinox.core.exceptions import ValidationError
from equinox.storage.utils import safe_json_loads

logger = logging.getLogger(__name__)


_SINGLE_BRACE_PATH_VAR_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_-]*)}(?!})")
_COLON_PATH_VAR_RE = re.compile(r"(^|/):([A-Za-z_][A-Za-z0-9_-]*)(?=/|$)")


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


def json_to_dict(
    raw: str,
    default: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Parse a JSON string → dict, handling both dict and list-of-pairs formats.

    When the stored value is a list (e.g. params stored as ``{key, value,
    enabled}`` objects) it is converted to a plain ``{key: value}`` dict for
    export use.

    Args:
        raw:     Raw JSON string to parse.
        default: Fallback value when *raw* is absent or unparseable.
                 Defaults to ``{}``.

    Returns:
        Parsed dict, or *default* on failure.
    """
    default = default or {}
    parsed = safe_json_loads(raw, default=default)
    if isinstance(parsed, list):
        return {
            item.get("key", ""): item.get("value", "")
            for item in parsed
            if isinstance(item, dict)
        }
    return parsed if isinstance(parsed, dict) else default


def parse_url_parts(url: str) -> Dict[str, str]:
    """Safely parse *url* into its export-friendly components.

    Returns a dict with keys ``scheme``, ``hostname``, ``port``, ``path``,
    ``query``, and ``netloc``, falling back to safe defaults on any parse
    failure.  This is distinct from :func:`equinox.core.urls._parse_url`,
    which returns a ``(scheme, netloc, path, query)`` tuple.

    Args:
        url: URL string to decompose.

    Returns:
        Dict with the URL's components.
    """
    try:
        p = urls.url_metadata(url)
        return {
            "scheme":   str(p.get("scheme") or "https"),
            "hostname": str(p.get("hostname") or ""),
            "port":     str(p.get("port") or ""),
            "path":     str(p.get("path") or "/"),
            "query":    str(p.get("query") or ""),
            "netloc":   str(p.get("netloc") or ""),
        }
    except Exception as exc:
        logger.warning("Failed to parse URL %s: %s", url, exc)
        return {"scheme": "https", "hostname": "", "port": "", "path": "/", "query": "", "netloc": ""}


def write_json_file(data: Dict[str, Any], file_path: Path) -> None:
    """Write *data* as pretty-printed JSON to *file_path*.

    Creates any missing parent directories automatically.

    Args:
        data:      Serialisable dict to write.
        file_path: Destination path (parents created if absent).

    Raises:
        IOError: If the file cannot be written.
    """
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Exported to %s", file_path)
    except IOError as exc:
        logger.error("Failed to write %s: %s", file_path, exc)
        raise


def normalize_path_variables(url: str) -> str:
    """Normalize path variables to Equinox ``{{var}}`` format.

    Supported input syntaxes:
    - OpenAPI style ``/users/{id}``
    - Colon style ``/users/:id``
    """
    if not isinstance(url, str) or not url:
        return url

    normalized = _SINGLE_BRACE_PATH_VAR_RE.sub(r"{{\1}}", url)
    normalized = _COLON_PATH_VAR_RE.sub(r"\1{{\2}}", normalized)
    return normalized


