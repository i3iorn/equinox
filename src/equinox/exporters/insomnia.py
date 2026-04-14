"""Insomnia v4 exporter."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from equinox.core.time import to_iso_z
from equinox.storage.database import Database
from equinox.exporters._prepared import _BaseCollectionExporter, _PreparedRequest

__all__ = ["InsomniaExporter"]

logger = logging.getLogger(__name__)


class InsomniaExporter(_BaseCollectionExporter):
    """Export collections in Insomnia v4 format.

    The exported document contains one ``request_group`` (folder) resource
    representing the collection, plus one ``request`` resource per request
    in the collection.
    """

    @staticmethod
    def export_collection(
        db: Database,
        collection_id: int,
    ) -> Dict[str, Any]:
        """Export *collection_id* as an Insomnia v4 dict.

        Args:
            db:            Open database connection.
            collection_id: ID of the collection to export.

        Returns:
            A dict conforming to the Insomnia v4 export schema.

        Raises:
            ValidationError: If the collection is not found.
        """
        collection, requests = InsomniaExporter._load_collection(db, collection_id)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        resources: List[Dict[str, Any]] = [
            InsomniaExporter._build_resource(req, idx, now_ms)
            for idx, req in enumerate(requests)
        ]
        resources.append({
            "_id":                      "fld_root",
            "_type":                    "request_group",
            "name":                     collection.get("name", ""),
            "description":              collection.get("description", ""),
            "environment":              {},
            "environmentPropertyOrder": None,
            "metaSortKey":              -1,
        })

        return {
            "_type":           "export",
            "__export_format": 4,
            "__export_date":   to_iso_z(),
            "__export_source": "equinox.api",
            "resources":       resources,
        }

    @staticmethod
    def _build_resource(
        req: _PreparedRequest,
        idx: int,
        now_ms: int,
    ) -> Dict[str, Any]:
        """Build a single Insomnia request resource dict from *req*.

        Args:
            req:    Prepared request snapshot.
            idx:    Zero-based position in the collection (used for ``_id``).
            now_ms: Current timestamp in milliseconds (``modified``/``created``).

        Returns:
            Insomnia request resource dict.
        """
        resource: Dict[str, Any] = {
            "_id":            f"req_{idx}",
            "_type":          "request",
            "parentId":       "fld_root",
            "modified":       now_ms,
            "created":        now_ms,
            "name":           req.name,
            "description":    req.description,
            "method":         req.method,
            "url":            req.raw_url,
            "authentication": {},
            "parameters":     [{"name": k, "value": v} for k, v in req.params.items()],
            "headers":        [{"name": k, "value": v} for k, v in req.headers.items()],
            "body":           None,
        }
        if req.body:
            resource["body"] = {"mimeType": req.content_type, "text": req.body}
        return resource

