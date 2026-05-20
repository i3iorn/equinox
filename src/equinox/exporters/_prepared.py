"""Shared infrastructure for collection-based exporters.

:class:`_PreparedRequest` normalises a raw DB row into an export-ready
snapshot so that JSON-parsing and URL-parsing logic live in exactly one
place.  :class:`_BaseCollectionExporter` provides the common
``_load_collection`` / ``export_to_file`` helpers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from equinox.core import urls
from equinox.core.exceptions import ValidationError
from equinox.importers._utils import json_to_dict as _json_to_dict
from equinox.importers._utils import parse_url_parts, write_json_file
from equinox.security import redact_headers
from equinox.storage.collections import CollectionManager
from equinox.storage.database import Database

__all__ = ["_PreparedRequest", "_BaseCollectionExporter"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _PreparedRequest
# ---------------------------------------------------------------------------


@dataclass
class _PreparedRequest:
    """Normalised, export-ready view of a raw DB request row.

    Created once per row and consumed by all exporters so that JSON-parsing
    and URL-parsing logic live in exactly one place.

    Attributes:
        name:         Display name of the request.
        description:  Optional description.
        method:       HTTP verb (upper-case).
        raw_url:      Original URL string (may contain ``{{VAR}}`` tokens).
        body:         Request body text, or ``None`` when absent.
        headers:      Redacted header dict (sensitive values replaced).
        params:       Query-parameter dict.
        path_params:  Path-parameter dict.
        auth_obj:     Parsed auth dict; empty when absent or unparseable.
        content_type: Value of the ``Content-Type`` header (defaults to
                      ``"application/json"`` when the header is absent).
        url_parts:    Components of the expanded URL — ``scheme``,
                      ``hostname``, ``port``, ``path``, ``query``,
                      ``netloc``.
    """

    name: str
    description: str
    method: str
    raw_url: str
    body: str | None
    headers: dict[str, str]
    params: dict[str, str]
    path_params: dict[str, str]
    auth_obj: dict[str, Any]
    content_type: str
    url_parts: dict[str, str]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> _PreparedRequest:
        """Build a :class:`_PreparedRequest` from a raw DB row dict."""
        headers = _json_to_dict(row.get("headers", "{}"))
        params = _json_to_dict(row.get("params", "{}"))
        path_params = _json_to_dict(row.get("path_params", "{}"))

        auth_obj: dict[str, Any] = {}
        auth_raw = row.get("auth")
        if auth_raw:
            try:
                auth_obj = _json_to_dict(auth_raw)
            except Exception as exc:
                logger.debug("Failed to parse auth for %r: %s", row.get("name"), exc)

        expanded = urls.expand_placeholders(row.get("url", ""), None)
        url_parts = parse_url_parts(expanded)

        return cls(
            name=row.get("name", "Unnamed"),
            description=row.get("description", ""),
            method=row.get("method", "GET"),
            raw_url=row.get("url", ""),
            body=row.get("body") or None,
            headers=dict(redact_headers(headers)),
            params=params,
            path_params=path_params,
            auth_obj=auth_obj,
            content_type=headers.get("Content-Type", "application/json"),
            url_parts=url_parts,
        )


# ---------------------------------------------------------------------------
# _BaseCollectionExporter
# ---------------------------------------------------------------------------


class _BaseCollectionExporter:
    """Shared helpers for exporters that work with a DB collection.

    Subclasses get ``_load_collection`` (validates and fetches rows) and
    ``export_to_file`` (pretty-prints to disk) for free.
    """

    @staticmethod
    def _load_collection(
        db: Database,
        collection_id: int,
    ) -> tuple[dict[str, Any], list[_PreparedRequest]]:
        """Load and validate *collection_id*, returning its prepared rows.

        Args:
            db:            Open database connection.
            collection_id: ID of the collection to load.

        Returns:
            ``(collection_dict, [_PreparedRequest, …])``

        Raises:
            ValidationError: If no collection with *collection_id* exists.
        """
        manager = CollectionManager(db)
        collection = manager.get_collection(collection_id)
        if not collection:
            raise ValidationError(f"Collection {collection_id} not found")
        raw = manager.list_requests_in_collection(collection_id)
        return collection, [_PreparedRequest.from_row(r) for r in raw]

    @staticmethod
    def export_to_file(data: dict[str, Any], file_path: Path) -> None:
        """Write *data* as pretty-printed JSON to *file_path*.

        Args:
            data:      Serialisable dict to export.
            file_path: Destination; parent directories are created if absent.

        Raises:
            IOError: If the file cannot be written.
        """
        write_json_file(data, file_path)
