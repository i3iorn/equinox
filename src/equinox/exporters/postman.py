"""Postman Collection v2.1 exporter."""

from __future__ import annotations

import logging
import warnings
from typing import Any

from equinox.exporters._prepared import _BaseCollectionExporter, _PreparedRequest
from equinox.storage.collections import CollectionManager
from equinox.storage.database import Database

__all__ = ["PostmanExporter"]

logger = logging.getLogger(__name__)

# Placeholder used in place of all secret values so exported files are safe
# to share or commit to VCS.
_REDACTED = "[REDACTED]"


class PostmanExporter(_BaseCollectionExporter):
    """Export collections in Postman Collection v2.1 format.

    Secret credential values (tokens, passwords, API keys) are always
    replaced with ``"[REDACTED]"`` so the exported file is safe to share.
    """

    @staticmethod
    def export_collection(
        db: Database,
        collection_id: int,
        include_history: bool = False,
    ) -> dict[str, Any]:
        """Export *collection_id* as a Postman v2.1 dict.

        Args:
            db:              Open database connection.
            collection_id:   ID of the collection to export.
            include_history: Deprecated / reserved.  Raises a ``FutureWarning``
                when ``True``; history export is not yet implemented.

        Returns:
            A dict conforming to the Postman Collection v2.1 schema.

        Raises:
            ValidationError: If the collection is not found.
        """
        if include_history:
            warnings.warn(
                "PostmanExporter.export_collection: include_history=True has no effect — "
                "history export is not yet implemented.",
                FutureWarning,
                stacklevel=2,
            )
            logger.warning(
                "PostmanExporter.export_collection called with include_history=True, "
                "which is not yet implemented and has been ignored.",
            )

        collection, requests = PostmanExporter._load_collection(db, collection_id)
        items: list[dict[str, Any]] = [PostmanExporter._build_item(req) for req in requests]

        variables: list[dict[str, Any]] = []
        try:
            var_list = CollectionManager(db).list_collection_variables(collection_id)
            # Storage schema uses "key"/"value" columns — not "name"/"value".
            variables = [{"key": v["key"], "value": v["value"], "type": "string"} for v in var_list]
        except Exception as exc:
            logger.warning(
                "Failed to export collection variables (%d variable(s) skipped): %s",
                len(variables),
                exc,
            )

        return {
            "info": {
                "_postman_id": collection.get("id", ""),
                "name": collection.get("name", ""),
                "description": collection.get("description", ""),
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": items,
            "variable": variables,
        }

    @staticmethod
    def _build_item(req: _PreparedRequest) -> dict[str, Any]:
        """Build a single Postman item dict from *req*."""
        parts = req.url_parts
        hostname = parts["hostname"]

        item: dict[str, Any] = {
            "name": req.name,
            "request": {
                "method": req.method,
                "header": [{"key": k, "value": v, "type": "text"} for k, v in req.headers.items()],
                "url": {
                    "raw": req.raw_url,
                    "protocol": parts["scheme"],
                    "host": hostname.split(".") if hostname else [],
                    "port": parts["port"],
                    "path": [seg for seg in parts["path"].split("/") if seg],
                    "query": [
                        {"key": k, "value": v, "type": "text"} for k, v in req.params.items()
                    ],
                },
            },
        }

        if req.auth_obj:
            auth_block = PostmanExporter._build_auth(req.auth_obj)
            if auth_block:
                item["request"]["auth"] = auth_block

        if req.body:
            item["request"]["body"] = {
                "mode": "raw",
                "raw": req.body,
                "options": {"raw": {"language": "json" if "json" in req.content_type else "text"}},
            }

        return item

    @staticmethod
    def _build_auth(auth_obj: dict[str, Any]) -> dict[str, Any]:
        """Convert *auth_obj* to Postman v2.1 auth format.

        All secret values are replaced with ``_REDACTED`` so the exported
        file is safe to share or commit to VCS.

        Args:
            auth_obj: Parsed auth dict (from the ``auth_data`` DB column).

        Returns:
            Postman-formatted auth dict, or ``{}`` for unknown auth types.
        """
        auth_type = auth_obj.get("type", "").lower()
        if auth_type == "bearer":
            return {
                "type": "bearer",
                "bearer": [{"key": "token", "value": _REDACTED, "type": "string"}],
            }
        if auth_type == "apikey":
            return {
                "type": "apikey",
                "apikey": [
                    {"key": "key", "value": auth_obj.get("key", ""), "type": "string"},
                    {"key": "value", "value": _REDACTED, "type": "string"},
                    {"key": "in", "value": auth_obj.get("in", "header"), "type": "string"},
                ],
            }
        if auth_type == "basic":
            return {
                "type": "basic",
                "basic": [
                    {"key": "username", "value": auth_obj.get("username", ""), "type": "string"},
                    {"key": "password", "value": _REDACTED, "type": "string"},
                ],
            }
        if auth_type == "oauth2":
            return {
                "type": "oauth2",
                "oauth2": [
                    {
                        "key": "grant_type",
                        "value": auth_obj.get("grant_type", ""),
                        "type": "string",
                    },
                    {"key": "tokenUrl", "value": auth_obj.get("token_url", ""), "type": "string"},
                ],
            }
        return {}
