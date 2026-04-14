"""Export functionality for collections, requests, and responses.

Supports:
- Postman Collection v2.1
- OpenAPI 3.0
- cURL commands
- Insomnia v4
- HAR (HTTP Archive)
"""
from __future__ import annotations

import json
import logging
import platform
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from equinox.core.request import Request, Response
from equinox.storage.collections import CollectionManager
from equinox.storage.database import Database
from equinox.core.exceptions import ValidationError
from equinox.core.redact import redact_headers
from equinox.core.validation import Validator
from equinox.core import urls
from equinox.storage.utils import safe_json_loads, coerce_body_to_str

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _json_to_dict(raw: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Parse a JSON string → dict, handling both dict and list-of-pairs formats.

    When the stored value is a list (e.g. params stored as ``{key, value, enabled}``
    objects) it is converted to a plain ``{key: value}`` dict for export use.
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


def _to_iso(dt: Optional[datetime] = None) -> str:
    """Return *dt* (or now) as an ISO 8601 string with trailing ``Z``.

    Uses ``strftime`` directly to avoid the ``+00:00`` suffix produced by
    ``datetime.isoformat()`` on timezone-aware objects.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_url(url: str) -> Dict[str, str]:
    """Safely parse *url* into its components, returning safe defaults on failure."""
    try:
        p = urlparse(url)
        return {
            "scheme":   p.scheme or "https",
            "hostname": p.hostname or "",
            "port":     str(p.port) if p.port else "",
            "path":     p.path or "/",
            "query":    p.query or "",
            "netloc":   p.netloc or "",
        }
    except Exception as exc:
        logger.warning("Failed to parse URL %s: %s", url, exc)
        return {"scheme": "https", "hostname": "", "port": "", "path": "/", "query": "", "netloc": ""}


def _write_json_file(data: Dict[str, Any], file_path: Path) -> None:
    """Write *data* as pretty-printed JSON to *file_path*."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Exported to %s", file_path)
    except IOError as exc:
        logger.error("Failed to write %s: %s", file_path, exc)
        raise


# ---------------------------------------------------------------------------
# Auth conversion helpers (pure functions — no class needed)
# ---------------------------------------------------------------------------

_REDACTED = "[REDACTED]"


def _postman_auth(auth_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an auth dict to Postman v2.1 auth format.

    Secret values (tokens, passwords, client_secrets) are always redacted
    so exported files are safe to share or commit to VCS.
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
                {"key": "key",   "value": auth_obj.get("key", ""),      "type": "string"},
                {"key": "value", "value": _REDACTED,                     "type": "string"},
                {"key": "in",    "value": auth_obj.get("in", "header"), "type": "string"},
            ],
        }
    if auth_type == "basic":
        return {
            "type": "basic",
            "basic": [
                {"key": "username", "value": auth_obj.get("username", ""), "type": "string"},
                {"key": "password", "value": _REDACTED,                    "type": "string"},
            ],
        }
    if auth_type == "oauth2":
        return {
            "type": "oauth2",
            "oauth2": [
                {"key": "grant_type", "value": auth_obj.get("grant_type", ""), "type": "string"},
                {"key": "tokenUrl",   "value": auth_obj.get("token_url", ""),  "type": "string"},
            ],
        }
    return {}


def _openapi_security_scheme(auth_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an auth dict to an OpenAPI 3.0 security scheme object."""
    auth_type = auth_obj.get("type", "").lower()
    if auth_type == "bearer":
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    if auth_type == "apikey":
        return {
            "type":  "apiKey",
            "name":  auth_obj.get("key", "X-API-Key"),
            "in":    auth_obj.get("in", "header"),
        }
    if auth_type == "basic":
        return {"type": "http", "scheme": "basic"}
    if auth_type == "oauth2":
        return {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": auth_obj.get("auth_url", ""),
                    "tokenUrl":         auth_obj.get("token_url", ""),
                    "scopes":           {},
                }
            },
        }
    return {}


# ---------------------------------------------------------------------------
# _PreparedRequest — normalized, export-ready view of a DB request row
# ---------------------------------------------------------------------------

@dataclass
class _PreparedRequest:
    """Normalized, export-ready view of a raw DB request row.

    Created once per row and consumed by all exporters, so JSON-parsing and
    URL-parsing logic live in exactly one place.
    """

    name:         str
    description:  str
    method:       str
    raw_url:      str
    body:         Optional[str]
    headers:      Dict[str, str]   # redacted
    params:       Dict[str, str]
    path_params:  Dict[str, str]
    auth_obj:     Dict[str, Any]   # empty when absent or unparseable
    content_type: str
    url_parts:    Dict[str, str]   # from _parse_url(expanded_url)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> _PreparedRequest:
        """Build a :class:`_PreparedRequest` from a raw DB row dict."""
        headers     = _json_to_dict(row.get("headers", "{}"))
        params      = _json_to_dict(row.get("params", "{}"))
        path_params = _json_to_dict(row.get("path_params", "{}"))

        auth_obj: Dict[str, Any] = {}
        auth_raw = row.get("auth")
        if auth_raw:
            try:
                auth_obj = _json_to_dict(auth_raw)
            except Exception as exc:
                logger.debug("Failed to parse auth for %r: %s", row.get("name"), exc)

        expanded  = urls.expand_placeholders(row.get("url", ""), None)
        url_parts = _parse_url(expanded)

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
    """Shared utilities for exporters that operate on a collection in the database."""

    @staticmethod
    def _load_collection(
        db: Database,
        collection_id: int,
    ) -> Tuple[Dict[str, Any], List[_PreparedRequest]]:
        """Load and validate a collection plus its prepared request rows.

        Raises:
            ValidationError: If the collection does not exist.
        """
        manager    = CollectionManager(db)
        collection = manager.get_collection(collection_id)
        if not collection:
            raise ValidationError(f"Collection {collection_id} not found")
        raw = manager.list_requests_in_collection(collection_id)
        return collection, [_PreparedRequest.from_row(r) for r in raw]

    @staticmethod
    def export_to_file(data: Dict[str, Any], file_path: Path) -> None:
        """Write *data* as pretty JSON to *file_path*."""
        _write_json_file(data, file_path)


# ---------------------------------------------------------------------------
# CurlExporter
# ---------------------------------------------------------------------------

class CurlExporter:
    """Export individual requests as cURL commands."""

    @staticmethod
    def _shell_quote(s: str) -> str:
        """Shell-escape *s* with platform-appropriate quoting.

        Uses ``shlex.quote`` on Unix and double-quotes (with internal-quote
        escaping) on Windows, where ``shlex.quote`` produces POSIX-style
        single-quotes that ``cmd.exe`` does not handle.
        """
        if platform.system() == "Windows":
            return '"' + s.replace('"', '\\"') + '"'
        return shlex.quote(s)

    @staticmethod
    def export_request(request: Request) -> str:
        """Export *request* as a cURL command string.

        Raises:
            ValidationError: If the request URL is invalid.
        """
        Validator.validate_url(request.url)

        quote = CurlExporter._shell_quote
        parts = ["curl", "-X", request.method]

        if request.headers:
            for key, value in redact_headers(request.headers).items():
                parts.append(f"-H {quote(f'{key}: {value}')}")

        if request.body:
            parts.append(f"-d {quote(request.body)}")

        base_url = urls.expand_placeholders(
            request.url,
            getattr(request, "path_params", None) or None,
        )
        if request.params:
            sep = "&" if "?" in base_url else "?"
            qs  = "&".join(f"{k}={v}" for k, v in request.params.items())
            parts.append(quote(f"{base_url}{sep}{qs}"))
        else:
            parts.append(quote(base_url))

        return " ".join(parts)


# ---------------------------------------------------------------------------
# PostmanExporter
# ---------------------------------------------------------------------------

class PostmanExporter(_BaseCollectionExporter):
    """Export collections in Postman Collection v2.1 format."""

    @staticmethod
    def export_collection(
        db: Database,
        collection_id: int,
        include_history: bool = False,
    ) -> Dict[str, Any]:
        """Export *collection_id* as a Postman v2.1 dict.

        Raises:
            ValidationError: If the collection is not found.
        """
        collection, requests = PostmanExporter._load_collection(db, collection_id)
        items = [PostmanExporter._build_item(req) for req in requests]

        variables: List[Dict[str, Any]] = []
        try:
            var_list  = CollectionManager(db).list_collection_variables(collection_id)
            variables = [
                {"key": v["name"], "value": v["value"], "type": "string"}
                for v in var_list
            ]
        except Exception as exc:
            logger.warning("Failed to export collection variables: %s", exc)

        return {
            "info": {
                "_postman_id": collection.get("id", ""),
                "name":        collection.get("name", ""),
                "description": collection.get("description", ""),
                "schema":      "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item":     items,
            "variable": variables,
        }

    @staticmethod
    def _build_item(req: _PreparedRequest) -> Dict[str, Any]:
        """Build a single Postman item dict from a prepared request."""
        parts    = req.url_parts
        hostname = parts["hostname"]

        item: Dict[str, Any] = {
            "name": req.name,
            "request": {
                "method": req.method,
                "header": [
                    {"key": k, "value": v, "type": "text"}
                    for k, v in req.headers.items()
                ],
                "url": {
                    "raw":      req.raw_url,
                    "protocol": parts["scheme"],
                    "host":     hostname.split(".") if hostname else [],
                    "port":     parts["port"],
                    "path":     [seg for seg in parts["path"].split("/") if seg],
                    "query": [
                        {"key": k, "value": v, "type": "text"}
                        for k, v in req.params.items()
                    ],
                },
            },
        }

        if req.auth_obj:
            auth_export = _postman_auth(req.auth_obj)
            if auth_export:
                item["request"]["auth"] = auth_export

        if req.body:
            item["request"]["body"] = {
                "mode": "raw",
                "raw":  req.body,
                "options": {
                    "raw": {"language": "json" if "json" in req.content_type else "text"}
                },
            }

        return item


# ---------------------------------------------------------------------------
# OpenAPIExporter
# ---------------------------------------------------------------------------

class OpenAPIExporter(_BaseCollectionExporter):
    """Export collections as OpenAPI 3.0 specifications."""

    @staticmethod
    def export_collection(
        db: Database,
        collection_id: int,
        title: str = "API",
        version: str = "1.0.0",
    ) -> Dict[str, Any]:
        """Export *collection_id* as an OpenAPI 3.0 dict.

        Raises:
            ValidationError: If the collection is not found.
        """
        collection, requests = OpenAPIExporter._load_collection(db, collection_id)

        paths:            Dict[str, Any] = {}
        security_schemes: Dict[str, Any] = {}

        for req in requests:
            path   = req.url_parts["path"] or "/"
            method = req.method.lower()
            paths.setdefault(path, {})[method] = OpenAPIExporter._build_operation(req)

            # Collect security scheme in the same pass — no second iteration needed.
            auth_type = req.auth_obj.get("type", "").lower()
            if auth_type and auth_type not in security_schemes:
                scheme = _openapi_security_scheme(req.auth_obj)
                if scheme:
                    security_schemes[auth_type] = scheme

        spec: Dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {
                "title":       title,
                "version":     version,
                "description": collection.get("description", ""),
            },
            "paths": paths,
        }
        if security_schemes:
            spec["components"] = {"securitySchemes": security_schemes}

        return spec

    @staticmethod
    def _build_operation(req: _PreparedRequest) -> Dict[str, Any]:
        """Build a single OpenAPI path-operation dict from a prepared request."""
        parameters = [
            {"name": k, "in": "query", "required": False,
             "schema": {"type": "string"}, "example": v}
            for k, v in req.params.items()
        ] + [
            {"name": k, "in": "path", "required": True,
             "schema": {"type": "string"}, "example": v}
            for k, v in req.path_params.items()
        ]

        operation: Dict[str, Any] = {
            "summary":     req.name,
            "description": req.description,
            "parameters":  parameters,
            "responses": {
                "200": {
                    "description": "Successful response",
                    "content":     {req.content_type: {}},
                }
            },
        }

        if req.body:
            operation["requestBody"] = {
                "required": True,
                "content":  {req.content_type: {"schema": {"type": "object"}}},
            }

        auth_type = req.auth_obj.get("type", "").lower()
        if auth_type:
            operation["security"] = [{auth_type: []}]

        return operation


# ---------------------------------------------------------------------------
# HARExporter
# ---------------------------------------------------------------------------

class HARExporter:
    """Export request/response pairs as HAR (HTTP Archive) format."""

    @staticmethod
    def export_request_response(
        request: Request,
        response: Response,
        started_datetime: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Build a single HAR entry from *request* and *response*.

        Args:
            started_datetime: Request start time (defaults to now).
        """
        safe_req_headers  = redact_headers(request.headers or {})
        safe_resp_headers = redact_headers(dict(response.headers) if response.headers else {})
        resp_body         = coerce_body_to_str(response.body) if response.body else ""
        req_content_type  = (request.headers or {}).get(
            "Content-Type", "application/x-www-form-urlencoded"
        )
        elapsed_ms = int(response.elapsed * 1000) if response.elapsed else 0

        return {
            "startedDateTime": _to_iso(started_datetime),
            "time":            elapsed_ms,
            "request": {
                "method":      request.method,
                "url":         request.url,
                "httpVersion": "HTTP/1.1",
                "headers":     [{"name": k, "value": v} for k, v in safe_req_headers.items()],
                "queryString": [{"name": k, "value": v} for k, v in (request.params or {}).items()],
                "postData":    {"mimeType": req_content_type, "text": request.body or ""} if request.body else None,
                "cookies":     [],
                "headersSize": sum(len(f"{k}: {v}\r\n") for k, v in safe_req_headers.items()),
                "bodySize":    len(request.body) if request.body else 0,
            },
            "response": {
                "status":      response.status_code,
                "statusText":  response.reason or "",
                "httpVersion": "HTTP/1.1",
                "headers":     [{"name": k, "value": v} for k, v in safe_resp_headers.items()],
                "cookies":     [],
                "content": {
                    "size":     len(response.body) if response.body else 0,
                    "mimeType": (response.headers or {}).get("Content-Type", "application/octet-stream"),
                    "text":     resp_body,
                },
                "redirectURL": (response.headers or {}).get("Location", "") if response.headers else "",
                "headersSize": sum(len(f"{k}: {v}\r\n") for k, v in safe_resp_headers.items()),
                "bodySize":    len(response.body) if response.body else 0,
            },
            "cache":   {},
            "timings": {
                "blocked": -1,
                "dns":     -1,
                "connect": -1,
                "send":    -1,
                "wait":    elapsed_ms,
                "receive": -1,
                "ssl":     -1,
            },
        }

    @staticmethod
    def create_har_archive(
        entries: List[Dict[str, Any]],
        title: str = "Equinox Archive",
    ) -> Dict[str, Any]:
        """Wrap *entries* in a standard HAR log envelope."""
        return {
            "log": {
                "version": "1.2",
                "creator": {"name": "Equinox", "version": "0.1.0"},
                "entries": entries,
            }
        }

    @staticmethod
    def export_to_file(har_dict: Dict[str, Any], file_path: Path) -> None:
        """Write the HAR archive to *file_path*."""
        _write_json_file(har_dict, file_path)


# ---------------------------------------------------------------------------
# InsomniaExporter
# ---------------------------------------------------------------------------

class InsomniaExporter(_BaseCollectionExporter):
    """Export collections in Insomnia v4 format."""

    @staticmethod
    def export_collection(
        db: Database,
        collection_id: int,
    ) -> Dict[str, Any]:
        """Export *collection_id* as an Insomnia v4 dict.

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
            "_type":            "export",
            "__export_format":  4,
            "__export_date":    _to_iso(),
            "__export_source":  "equinox.api",
            "resources":        resources,
        }

    @staticmethod
    def _build_resource(
        req: _PreparedRequest, idx: int, now_ms: int
    ) -> Dict[str, Any]:
        """Build a single Insomnia resource dict from a prepared request."""
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
