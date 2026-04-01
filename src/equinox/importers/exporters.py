"""Export functionality for collections, requests, and responses.

Supports multiple export formats:
- Postman Collection Format (v2.1)
- OpenAPI/Swagger (v3.0)
- cURL commands
- Insomnia v4
- HAR (HTTP Archive)

## Improvements:
1. Input validation (zero-trust)
2. Windows-aware shell quoting
3. Safe URL parsing via urllib.parse
4. Auth export support
5. Path parameters export
6. Code deduplication (write_json_file utility)
7. Consistent response body encoding
8. Comprehensive error handling
9. Safe JSON parsing utility
10. Collection variables export
11. Content-Type inference
12. Better timestamp handling
"""

import json
import logging
import platform
import shlex
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime, timezone
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


def _json_to_dict(raw: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Parse JSON string to dict, handling both dict and list (params_list) formats.

    Delegates to the canonical :func:`~equinox.storage.utils.safe_json_loads`
    for actual JSON parsing.  When the stored value is a list (params_list
    format with ``{key, value, enabled}`` objects) it is converted to a plain
    ``{key: value}`` dict for export use.
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


def _get_iso_timestamp(dt: Optional[datetime] = None) -> str:
    """Get ISO 8601 timestamp with Z suffix. (Improvement #12)
    
    Args:
        dt: Datetime to format (defaults to now)
        
    Returns:
        ISO timestamp string with Z suffix
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.isoformat() + "Z" if not dt.isoformat().endswith("Z") else dt.isoformat()


def _parse_url_safe(url: str) -> Dict[str, str]:
    """Safely parse URL into components. (Improvement #3)
    
    Args:
        url: URL to parse
        
    Returns:
        Dict with 'scheme', 'hostname', 'port', 'path', 'query'
    """
    try:
        parsed = urlparse(url)
        return {
            "scheme": parsed.scheme or "https",
            "hostname": parsed.hostname or "",
            "port": str(parsed.port) if parsed.port else "",
            "path": parsed.path or "/",
            "query": parsed.query or "",
            "netloc": parsed.netloc or "",
        }
    except Exception as e:
        logger.warning("Failed to parse URL %s: %s", url, e)
        return {
            "scheme": "https",
            "hostname": "",
            "port": "",
            "path": "/",
            "query": "",
            "netloc": "",
        }


def _write_json_file(data: Dict[str, Any], file_path: Path) -> None:
    """Write JSON data to file with error handling. (Improvement #6)
    
    Args:
        data: Data to write
        file_path: Path to save file
        
    Raises:
        IOError: If write fails
    """
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Exported to %s", file_path)
    except IOError as e:
        logger.error("Failed to write %s: %s", file_path, e)
        raise


class CurlExporter:
    """Export requests as cURL commands."""

    @staticmethod
    def _shell_quote(s: str) -> str:
        """Shell-escape a string with platform-aware quoting. (Improvement #2)
        
        Uses double quotes on Windows (requires internal quote escaping),
        and shlex.quote() on Unix systems.
        
        Args:
            s: String to quote
            
        Returns:
            Properly quoted string
        """
        if platform.system() == "Windows":
            # Windows: use double quotes, escape internal quotes
            return '"' + s.replace('"', '\\"') + '"'
        else:
            # Unix: use shlex.quote
            return shlex.quote(s)

    @staticmethod
    def export_request(request: Request) -> str:
        """Export single request as cURL command.

        Args:
            request: Request to export

        Returns:
            cURL command string
            
        Raises:
            ValidationError: If request is invalid
        """
        # Validation (Improvement #1)
        try:
            Validator.validate_url(request.url)
        except ValidationError as e:
            logger.error("Invalid URL in cURL export: %s", e)
            raise

        curl_cmd = ["curl", "-X", request.method]

        # Add headers â€” redact sensitive values so exports are safe to share
        if request.headers:
            safe_headers = redact_headers(request.headers)
            for key, value in safe_headers.items():
                curl_cmd.append(f"-H {CurlExporter._shell_quote(f'{key}: {value}')}")

        # Add body if present
        if request.body:
            curl_cmd.append(f"-d {CurlExporter._shell_quote(request.body)}")

        # Add query params â€” expand placeholders first for preview
        base_url = urls.expand_placeholders(request.url, getattr(request, "path_params", None) or None)
        if request.params:
            param_str = "&".join(f"{k}={v}" for k, v in request.params.items())
            url = f"{base_url}?{param_str}" if "?" not in base_url else f"{base_url}&{param_str}"
        else:
            url = base_url

        # Add URL at end
        curl_cmd.append(CurlExporter._shell_quote(url))

        return " ".join(curl_cmd)


class PostmanExporter:
    """Export collections in Postman v2.1 format."""

    @staticmethod
    def _export_auth(auth_obj: Dict[str, Any]) -> Dict[str, Any]:
        """Convert auth object to Postman format. (Improvement #4)

        SECURITY: Secret values (tokens, passwords, client_secrets) are
        redacted so exported files are safe to share / commit to VCS.

        Args:
            auth_obj: Auth dict from request
            
        Returns:
            Postman auth dict with secrets redacted
        """
        if not auth_obj:
            return {}

        _REDACTED = "[REDACTED]"
        auth_type = auth_obj.get("type", "").lower()
        
        if auth_type == "bearer":
            return {
                "type": "bearer",
                "bearer": [{"key": "token", "value": _REDACTED, "type": "string"}]
            }
        elif auth_type == "apikey":
            return {
                "type": "apikey",
                "apikey": [
                    {"key": "key", "value": auth_obj.get("key", ""), "type": "string"},
                    {"key": "value", "value": _REDACTED, "type": "string"},
                    {"key": "in", "value": auth_obj.get("in", "header"), "type": "string"},
                ]
            }
        elif auth_type == "basic":
            return {
                "type": "basic",
                "basic": [
                    {"key": "username", "value": auth_obj.get("username", ""), "type": "string"},
                    {"key": "password", "value": _REDACTED, "type": "string"},
                ]
            }
        elif auth_type == "oauth2":
            return {
                "type": "oauth2",
                "oauth2": [
                    {"key": "grant_type", "value": auth_obj.get("grant_type", ""), "type": "string"},
                    {"key": "tokenUrl", "value": auth_obj.get("token_url", ""), "type": "string"},
                ]
            }
        
        return {}

    @staticmethod
    def export_collection(
        db: Database,
        collection_id: int,
        include_history: bool = False
    ) -> Dict[str, Any]:
        """Export collection in Postman format.

        Args:
            db: Database instance
            collection_id: Collection ID to export
            include_history: Whether to include execution history

        Returns:
            Postman collection dict
            
        Raises:
            ValidationError: If collection not found or invalid
        """
        try:
            manager = CollectionManager(db)
            collection = manager.get_collection(collection_id)

            if not collection:
                raise ValidationError(f"Collection {collection_id} not found")

            items = []
            requests = manager.list_requests_in_collection(collection_id)

            for req in requests:
                # Safe JSON parsing (Improvement #9)
                headers = _json_to_dict(req.get("headers", "{}"))
                params = _json_to_dict(req.get("params", "{}"))
                path_params = _json_to_dict(req.get("path_params", "{}"))  # Improvement #5
                auth_data = req.get("auth")
                
                # Parse URL safely after expanding placeholders (Improvement #3)
                expanded = urls.expand_placeholders(req.get("url", ""), None)
                parsed = urlparse(expanded)
                url_parts = {
                    "scheme": parsed.scheme or "https",
                    "hostname": parsed.hostname or "",
                    "port": str(parsed.port) if parsed.port else "",
                    "path": parsed.path or "/",
                    "query": parsed.query or "",
                    "netloc": parsed.netloc or "",
                }

                item = {
                    "name": req.get("name", "Unnamed"),
                    "request": {
                        "method": req.get("method", "GET"),
                        "header": [
                            {"key": k, "value": v, "type": "text"}
                            for k, v in redact_headers(headers).items()
                        ],
                        "url": {
                            "raw": req.get("url", ""),
                            "protocol": url_parts["scheme"],
                            "host": url_parts["hostname"].split(".") if url_parts["hostname"] else [],
                            "port": url_parts["port"],
                            "path": url_parts["path"].split("/")[1:],  # Split path into segments
                            "query": [
                                {"key": k, "value": v, "type": "text"}
                                for k, v in params.items()
                            ]
                        }
                    }
                }
                
                # Add auth if present (Improvement #4)
                if auth_data:
                    try:
                        auth_obj = _json_to_dict(auth_data)
                        auth_export = PostmanExporter._export_auth(auth_obj)
                        if auth_export:
                            item["request"]["auth"] = auth_export
                    except Exception as e:
                        logger.warning("Failed to export auth for request %s: %s", req.get('name'), e)

                if req.get("body"):
                    # Content-Type inference (Improvement #11)
                    content_type = headers.get("Content-Type", "application/json")
                    item["request"]["body"] = {
                        "mode": "raw",
                        "raw": req["body"],
                        "options": {"raw": {"language": "json" if "json" in content_type else "text"}}
                    }

                items.append(item)

            # Export collection variables (Improvement #10)
            variables = []
            try:
                var_list = manager.list_collection_variables(collection_id)
                variables = [
                    {"key": v["name"], "value": v["value"], "type": "string"}
                    for v in var_list
                ]
            except Exception as e:
                logger.warning("Failed to export collection variables: %s", e)

            return {
                "info": {
                    "_postman_id": collection.get("id", ""),
                    "name": collection.get("name", ""),
                    "description": collection.get("description", ""),
                    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
                },
                "item": items,
                "variable": variables
            }
        except Exception as e:
            logger.error("Failed to export collection %s: %s", collection_id, e)
            raise

    @staticmethod
    def export_to_file(collection_dict: Dict[str, Any], file_path: Path) -> None:
        """Export collection to Postman file.

        Args:
            collection_dict: Collection dict
            file_path: Path to save file
            
        Raises:
            IOError: If file write fails
        """
        _write_json_file(collection_dict, file_path)


class OpenAPIExporter:
    """Export requests/collections as OpenAPI 3.0 spec."""

    @staticmethod
    def _export_openapi_auth(auth_obj: Dict[str, Any]) -> Dict[str, Any]:
        """Convert auth object to OpenAPI security scheme. (Improvement #4)
        
        Args:
            auth_obj: Auth dict from request
            
        Returns:
            OpenAPI securityScheme dict
        """
        if not auth_obj:
            return {}
        
        auth_type = auth_obj.get("type", "").lower()
        
        if auth_type == "bearer":
            return {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT"
            }
        elif auth_type == "apikey":
            return {
                "type": "apiKey",
                "name": auth_obj.get("key", "X-API-Key"),
                "in": auth_obj.get("in", "header")
            }
        elif auth_type == "basic":
            return {
                "type": "http",
                "scheme": "basic"
            }
        elif auth_type == "oauth2":
            return {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": auth_obj.get("auth_url", ""),
                        "tokenUrl": auth_obj.get("token_url", ""),
                        "scopes": {}
                    }
                }
            }
        
        return {}

    @staticmethod
    def export_collection(
        db: Database,
        collection_id: int,
        title: str = "API",
        version: str = "1.0.0"
    ) -> Dict[str, Any]:
        """Export collection as OpenAPI spec.

        Args:
            db: Database instance
            collection_id: Collection ID to export
            title: API title
            version: API version

        Returns:
            OpenAPI spec dict
            
        Raises:
            ValidationError: If collection not found or invalid
        """
        try:
            manager = CollectionManager(db)
            collection = manager.get_collection(collection_id)

            if not collection:
                raise ValidationError(f"Collection {collection_id} not found")

            paths = {}
            security_schemes = {}
            requests = manager.list_requests_in_collection(collection_id)

            for req in requests:
                url = req.get("url", "")
                method = req.get("method", "GET").lower()
                
                # Parse URL safely after expanding placeholders (Improvement #3)
                expanded = urls.expand_placeholders(url, None)
                parsed = urlparse(expanded)
                path = parsed.path or "/"

                # Safe JSON parsing (Improvement #9)
                headers = _json_to_dict(req.get("headers", "{}"))
                params = _json_to_dict(req.get("params", "{}"))
                path_params = _json_to_dict(req.get("path_params", "{}"))  # Improvement #5
                auth_data = req.get("auth")

                if path not in paths:
                    paths[path] = {}

                # Content-Type inference (Improvement #11)
                content_type = headers.get("Content-Type", "application/json")
                
                operation = {
                    "summary": req.get("name", ""),
                    "description": req.get("description", ""),
                    "parameters": [
                        {
                            "name": k,
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                            "example": v
                        }
                        for k, v in params.items()
                    ] + [
                        # Path parameters (Improvement #5)
                        {
                            "name": k,
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": v
                        }
                        for k, v in path_params.items()
                    ],
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "content": {content_type: {}}
                        }
                    }
                }

                if req.get("body"):
                    operation["requestBody"] = {
                        "required": True,
                        "content": {
                            content_type: {
                                "schema": {"type": "object"}
                            }
                        }
                    }
                
                # Add auth to operation if present (Improvement #4)
                if auth_data:
                    try:
                        auth_obj = _json_to_dict(auth_data)
                        auth_type = auth_obj.get("type", "").lower()
                        if auth_type:
                            operation["security"] = [{auth_type: []}]
                    except Exception as e:
                        logger.warning("Failed to process auth for request %s: %s", req.get('name'), e)

                paths[path][method] = operation
            
            # Build security schemes from all requests (Improvement #4)
            for req in requests:
                auth_data = req.get("auth")
                if auth_data:
                    try:
                        auth_obj = _json_to_dict(auth_data)
                        auth_type = auth_obj.get("type", "").lower()
                        if auth_type and auth_type not in security_schemes:
                            scheme = OpenAPIExporter._export_openapi_auth(auth_obj)
                            if scheme:
                                security_schemes[auth_type] = scheme
                    except Exception as e:
                        logger.warning("Failed to export auth scheme: %s", e)

            spec = {
                "openapi": "3.0.0",
                "info": {
                    "title": title,
                    "version": version,
                    "description": collection.get("description", "")
                },
                "paths": paths
            }
            
            if security_schemes:
                spec["components"] = {"securitySchemes": security_schemes}
            
            return spec
        except Exception as e:
            logger.error("Failed to export collection %s: %s", collection_id, e)
            raise

    @staticmethod
    def export_to_file(spec_dict: Dict[str, Any], file_path: Path) -> None:
        """Export OpenAPI spec to file.

        Args:
            spec_dict: OpenAPI spec dict
            file_path: Path to save file
            
        Raises:
            IOError: If file write fails
        """
        _write_json_file(spec_dict, file_path)


class HARExporter:
    """Export requests/responses as HAR (HTTP Archive) format."""

    @staticmethod
    def export_request_response(
        request: Request,
        response: Response,
        started_datetime: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Export request/response as HAR entry.

        Args:
            request: HTTP request
            response: HTTP response
            started_datetime: Request start time

        Returns:
            HAR entry dict
        """
        # Better timestamp handling (Improvement #12)
        if started_datetime is None:
            started_datetime = datetime.now(timezone.utc)
        
        safe_req_headers = redact_headers(request.headers or {})
        safe_resp_headers = redact_headers(dict(response.headers) if response.headers else {})
        
        # Consistent body encoding (Improvement #7)
        resp_body = coerce_body_to_str(response.body) if response.body else ""

        req_content_type = (request.headers or {}).get("Content-Type", "application/x-www-form-urlencoded") if request.headers else "application/x-www-form-urlencoded"

        return {
            "startedDateTime": _get_iso_timestamp(started_datetime),  # Improvement #12
            "time": response.elapsed * 1000 if response.elapsed else 0,
            "request": {
                "method": request.method,
                "url": request.url,
                "httpVersion": "HTTP/1.1",
                "headers": [
                    {"name": k, "value": v}
                    for k, v in safe_req_headers.items()
                ],
                "queryString": [
                    {"name": k, "value": v}
                    for k, v in (request.params or {}).items()
                ],
                "postData": {
                    "mimeType": req_content_type,
                    "text": request.body or ""
                } if request.body else None,
                "cookies": [],
                "headersSize": sum(len(f"{k}: {v}\r\n") for k, v in safe_req_headers.items()),
                "bodySize": len(request.body) if request.body else 0
            },
            "response": {
                "status": response.status_code,
                "statusText": response.reason or "",
                "httpVersion": "HTTP/1.1",
                "headers": [
                    {"name": k, "value": v}
                    for k, v in safe_resp_headers.items()
                ],
                "cookies": [],
                "content": {
                    "size": len(response.body) if response.body else 0,
                    "mimeType": (response.headers or {}).get("Content-Type", "application/octet-stream") if response.headers else "application/octet-stream",
                    "text": resp_body  # Uses canonical coerce_body_to_str (Improvement #7)
                },
                "redirectURL": (response.headers or {}).get("Location", "") if response.headers else "",
                "headersSize": sum(len(f"{k}: {v}\r\n") for k, v in safe_resp_headers.items()),
                "bodySize": len(response.body) if response.body else 0
            },
            "cache": {},
            "timings": {
                "blocked": -1,
                "dns": -1,
                "connect": -1,
                "send": -1,
                "wait": int(response.elapsed * 1000) if response.elapsed else -1,
                "receive": -1,
                "ssl": -1
            }
        }

    @staticmethod
    def create_har_archive(
        entries: List[Dict[str, Any]],
        title: str = "Equinox Archive"
    ) -> Dict[str, Any]:
        """Create HAR archive with entries.

        Args:
            entries: List of HAR entries
            title: Archive title

        Returns:
            HAR archive dict
        """
        return {
            "log": {
                "version": "1.2",
                "creator": {
                    "name": "Equinox",
                    "version": "0.1.0"
                },
                "entries": entries
            }
        }

    @staticmethod
    def export_to_file(har_dict: Dict[str, Any], file_path: Path) -> None:
        """Export HAR to file.

        Args:
            har_dict: HAR dict
            file_path: Path to save file
            
        Raises:
            IOError: If file write fails
        """
        _write_json_file(har_dict, file_path)


class InsomniaExporter:
    """Export collections in Insomnia v4 format."""

    @staticmethod
    def export_collection(
        db: Database,
        collection_id: int,
    ) -> Dict[str, Any]:
        """Export collection in Insomnia format.

        Args:
            db: Database instance
            collection_id: Collection ID to export

        Returns:
            Insomnia collection dict
            
        Raises:
            ValidationError: If collection not found or invalid
        """
        try:
            manager = CollectionManager(db)
            collection = manager.get_collection(collection_id)

            if not collection:
                raise ValidationError(f"Collection {collection_id} not found")

            resources = []
            requests = manager.list_requests_in_collection(collection_id)
            
            # Get current timestamp (Improvement #12)
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            for idx, req in enumerate(requests):
                # Safe JSON parsing (Improvement #9)
                headers = _json_to_dict(req.get("headers", "{}"))
                params = _json_to_dict(req.get("params", "{}"))
                
                # Content-Type inference (Improvement #11)
                content_type = headers.get("Content-Type", "application/json")
                
                resource = {
                    "_id": f"req_{idx}",
                    "_type": "request",
                    "parentId": "fld_root",
                    "modified": now_ms,
                    "created": now_ms,
                    "name": req.get("name", "Unnamed"),
                    "description": req.get("description", ""),
                    "method": req.get("method", "GET"),
                    "url": req.get("url", ""),
                    "authentication": {},
                    "parameters": [
                        {"name": k, "value": v}
                        for k, v in params.items()
                    ],
                    "headers": [
                        {"name": k, "value": v}
                        for k, v in redact_headers(headers).items()
                    ],
                    "body": {
                        "mimeType": content_type,
                        "text": req.get("body", "")
                    } if req.get("body") else None
                }
                resources.append(resource)

            resources.append({
                "_id": "fld_root",
                "_type": "request_group",
                "name": collection.get("name", ""),
                "description": collection.get("description", ""),
                "environment": {},
                "environmentPropertyOrder": None,
                "metaSortKey": -1
            })

            return {
                "_type": "export",
                "__export_format": 4,
                "__export_date": _get_iso_timestamp(),  # Improvement #12
                "__export_source": "equinox.api",
                "resources": resources
            }
        except Exception as e:
            logger.error("Failed to export collection %s: %s", collection_id, e)
            raise

    @staticmethod
    def export_to_file(collection_dict: Dict[str, Any], file_path: Path) -> None:
        """Export collection to Insomnia file.

        Args:
            collection_dict: Collection dict
            file_path: Path to save file
            
        Raises:
            IOError: If file write fails
        """
        _write_json_file(collection_dict, file_path)

