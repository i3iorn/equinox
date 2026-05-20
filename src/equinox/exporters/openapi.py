"""OpenAPI 3.0 specification exporter."""

from __future__ import annotations

import logging
from typing import Any

from equinox.exporters._prepared import _BaseCollectionExporter, _PreparedRequest
from equinox.storage.database import Database

__all__ = ["OpenAPIExporter"]

logger = logging.getLogger(__name__)


class OpenAPIExporter(_BaseCollectionExporter):
    """Export collections as OpenAPI 3.0 specifications.

    One ``path`` entry is generated per unique URL path across all requests.
    Auth types found in the collection are aggregated into
    ``components.securitySchemes`` automatically.
    """

    @staticmethod
    def export_collection(
        db: Database,
        collection_id: int,
        title: str = "API",
        version: str = "1.0.0",
    ) -> dict[str, Any]:
        """Export *collection_id* as an OpenAPI 3.0 dict.

        Args:
            db:            Open database connection.
            collection_id: ID of the collection to export.
            title:         Value of ``info.title`` (default ``"API"``).
            version:       Value of ``info.version`` (default ``"1.0.0"``).

        Returns:
            A dict conforming to the OpenAPI 3.0 schema.

        Raises:
            ValidationError: If the collection is not found.
        """
        collection, requests = OpenAPIExporter._load_collection(db, collection_id)

        paths: dict[str, Any] = {}
        security_schemes: dict[str, Any] = {}

        for req in requests:
            path = req.url_parts["path"] or "/"
            method = req.method.lower()
            paths.setdefault(path, {})[method] = OpenAPIExporter._build_operation(req)

            # Collect security schemes in a single pass — no second iteration.
            auth_type = req.auth_obj.get("type", "").lower()
            if auth_type and auth_type not in security_schemes:
                scheme = OpenAPIExporter._build_security_scheme(req.auth_obj)
                if scheme:
                    security_schemes[auth_type] = scheme

        spec: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {
                "title": title,
                "version": version,
                "description": collection.get("description", ""),
            },
            "paths": paths,
        }
        if security_schemes:
            spec["components"] = {"securitySchemes": security_schemes}

        return spec

    @staticmethod
    def _build_operation(req: _PreparedRequest) -> dict[str, Any]:
        """Build a single OpenAPI path-operation dict from *req*."""
        parameters: list[dict[str, Any]] = [
            {
                "name": k,
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "example": v,
            }
            for k, v in req.params.items()
        ] + [
            {"name": k, "in": "path", "required": True, "schema": {"type": "string"}, "example": v}
            for k, v in req.path_params.items()
        ]

        operation: dict[str, Any] = {
            "summary": req.name,
            "description": req.description,
            "parameters": parameters,
            "responses": {
                "200": {
                    "description": "Successful response",
                    "content": {req.content_type: {}},
                }
            },
        }

        if req.body:
            operation["requestBody"] = {
                "required": True,
                "content": {req.content_type: {"schema": {"type": "object"}}},
            }

        auth_type = req.auth_obj.get("type", "").lower()
        if auth_type:
            operation["security"] = [{auth_type: []}]

        return operation

    @staticmethod
    def _build_security_scheme(auth_obj: dict[str, Any]) -> dict[str, Any]:
        """Convert *auth_obj* to an OpenAPI 3.0 security scheme object.

        Args:
            auth_obj: Parsed auth dict (from the ``auth_data`` DB column).

        Returns:
            OpenAPI security scheme dict, or ``{}`` for unknown auth types.
        """
        auth_type = auth_obj.get("type", "").lower()
        if auth_type == "bearer":
            return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        if auth_type == "apikey":
            return {
                "type": "apiKey",
                "name": auth_obj.get("key", "X-API-Key"),
                "in": auth_obj.get("in", "header"),
            }
        if auth_type == "basic":
            return {"type": "http", "scheme": "basic"}
        if auth_type == "oauth2":
            return {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": auth_obj.get("auth_url", ""),
                        "tokenUrl": auth_obj.get("token_url", ""),
                        "scopes": {},
                    }
                },
            }
        return {}
