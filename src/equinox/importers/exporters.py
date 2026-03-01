"""Export functionality for collections, requests, and responses.

Supports multiple export formats:
- Postman Collection Format (v2.1)
- OpenAPI/Swagger (v3.0)
- cURL commands
- Insomnia v4
- HAR (HTTP Archive)
"""

import json
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime, timezone

from equinox.core.request import Request, Response
from equinox.storage.collections import CollectionManager
from equinox.storage.database import Database
from equinox.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class CurlExporter:
    """Export requests as cURL commands."""

    @staticmethod
    def _shell_quote(s: str) -> str:
        """Shell-escape a string using single quotes (safe on all platforms)."""
        # Replace each ' with '\'' (end quote, escaped quote, start quote)
        return "'" + s.replace("'", "'\\''") + "'"

    @staticmethod
    def export_request(request: Request) -> str:
        """Export single request as cURL command.

        Args:
            request: Request to export

        Returns:
            cURL command string
        """
        curl_cmd = ["curl", "-X", request.method]

        # Add headers
        if request.headers:
            for key, value in request.headers.items():
                curl_cmd.append(f"-H {CurlExporter._shell_quote(f'{key}: {value}')}")

        # Add body if present
        if request.body:
            curl_cmd.append(f"-d {CurlExporter._shell_quote(request.body)}")

        # Add query params
        if request.params:
            param_str = "&".join(f"{k}={v}" for k, v in request.params.items())
            url = f"{request.url}?{param_str}" if "?" not in request.url else f"{request.url}&{param_str}"
        else:
            url = request.url

        # Add URL at end
        curl_cmd.append(CurlExporter._shell_quote(url))

        return " ".join(curl_cmd)


class PostmanExporter:
    """Export collections in Postman v2.1 format."""

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
        """
        manager = CollectionManager(db)
        collection = manager.get_collection(collection_id)

        if not collection:
            raise ValidationError(f"Collection {collection_id} not found")

        items = []
        requests = manager.list_requests_in_collection(collection_id)

        for req in requests:
            item = {
                "name": req.get("name", "Unnamed"),
                "request": {
                    "method": req.get("method", "GET"),
                    "header": [
                        {"key": k, "value": v, "type": "text"}
                        for k, v in (json.loads(req.get("headers", "{}")) or {}).items()
                    ],
                    "url": {
                        "raw": req.get("url", ""),
                        "protocol": req.get("url", "").split("://")[0] if "://" in req.get("url", "") else "https",
                        "host": req.get("url", "").split("/")[2] if "//" in req.get("url", "") else "",
                        "query": [
                            {"key": k, "value": v, "type": "text"}
                            for k, v in (json.loads(req.get("params", "{}")) or {}).items()
                        ]
                    }
                }
            }

            if req.get("body"):
                item["request"]["body"] = {
                    "mode": "raw",
                    "raw": req["body"]
                }

            items.append(item)

        return {
            "info": {
                "_postman_id": collection.get("id", ""),
                "name": collection.get("name", ""),
                "description": collection.get("description", ""),
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
            },
            "item": items,
            "variable": []
        }

    @staticmethod
    def export_to_file(collection_dict: Dict[str, Any], file_path: Path) -> None:
        """Export collection to Postman file.

        Args:
            collection_dict: Collection dict
            file_path: Path to save file
        """
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(collection_dict, f, indent=2, ensure_ascii=False)


class OpenAPIExporter:
    """Export requests/collections as OpenAPI 3.0 spec."""

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
        """
        manager = CollectionManager(db)
        collection = manager.get_collection(collection_id)

        if not collection:
            raise ValidationError(f"Collection {collection_id} not found")

        paths = {}
        requests = manager.list_requests_in_collection(collection_id)

        for req in requests:
            url = req.get("url", "")
            method = req.get("method", "GET").lower()

            # Parse URL to get path
            if "://" in url:
                path = "/" + url.split("/", 3)[-1]
            else:
                path = "/"

            if path not in paths:
                paths[path] = {}

            headers = json.loads(req.get("headers", "{}")) or {}
            params = json.loads(req.get("params", "{}")) or {}

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
                ],
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {"application/json": {}}
                    }
                }
            }

            if req.get("body"):
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        headers.get("Content-Type", "application/json"): {
                            "schema": {"type": "object"}
                        }
                    }
                }

            paths[path][method] = operation

        return {
            "openapi": "3.0.0",
            "info": {
                "title": title,
                "version": version,
                "description": collection.get("description", "")
            },
            "paths": paths
        }

    @staticmethod
    def export_to_file(spec_dict: Dict[str, Any], file_path: Path) -> None:
        """Export OpenAPI spec to file.

        Args:
            spec_dict: OpenAPI spec dict
            file_path: Path to save file
        """
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(spec_dict, f, indent=2, ensure_ascii=False)


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
        if started_datetime is None:
            started_datetime = datetime.now(timezone.utc).replace(tzinfo=None)

        return {
            "startedDateTime": started_datetime.isoformat() + "Z",
            "time": response.elapsed * 1000 if response.elapsed else 0,
            "request": {
                "method": request.method,
                "url": request.url,
                "httpVersion": "HTTP/1.1",
                "headers": [
                    {"name": k, "value": v}
                    for k, v in (request.headers or {}).items()
                ],
                "queryString": [
                    {"name": k, "value": v}
                    for k, v in (request.params or {}).items()
                ],
                "postData": {
                    "mimeType": request.headers.get("Content-Type", "application/x-www-form-urlencoded"),
                    "text": request.body or ""
                } if request.body else None,
                "cookies": [],
                "headersSize": sum(len(f"{k}: {v}\r\n") for k, v in (request.headers or {}).items()),
                "bodySize": len(request.body) if request.body else 0
            },
            "response": {
                "status": response.status_code,
                "statusText": response.reason or "",
                "httpVersion": "HTTP/1.1",
                "headers": [
                    {"name": k, "value": v}
                    for k, v in (response.headers or {}).items()
                ],
                "cookies": [],
                "content": {
                    "size": len(response.body) if response.body else 0,
                    "mimeType": response.headers.get("Content-Type", "application/octet-stream"),
                    "text": response.body.decode('utf-8', errors='replace') if isinstance(response.body, bytes) else response.body
                },
                "redirectURL": response.headers.get("Location", ""),
                "headersSize": sum(len(f"{k}: {v}\r\n") for k, v in (response.headers or {}).items()),
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
        """
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(har_dict, f, indent=2, ensure_ascii=False)


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
        """
        manager = CollectionManager(db)
        collection = manager.get_collection(collection_id)

        if not collection:
            raise ValidationError(f"Collection {collection_id} not found")

        resources = []
        requests = manager.list_requests_in_collection(collection_id)

        for idx, req in enumerate(requests):
            resource = {
                "_id": f"req_{idx}",
                "_type": "request",
                "parentId": "fld_root",
                "modified": int(datetime.now(timezone.utc).timestamp() * 1000),
                "created": int(datetime.now(timezone.utc).timestamp() * 1000),
                "name": req.get("name", "Unnamed"),
                "description": req.get("description", ""),
                "method": req.get("method", "GET"),
                "url": req.get("url", ""),
                "authentication": {},
                "parameters": [
                    {"name": k, "value": v}
                    for k, v in (json.loads(req.get("params", "{}")) or {}).items()
                ],
                "headers": [
                    {"name": k, "value": v}
                    for k, v in (json.loads(req.get("headers", "{}")) or {}).items()
                ],
                "body": {
                    "mimeType": "application/json",
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
            "__export_date": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "__export_source": "equinox.api",
            "resources": resources
        }

    @staticmethod
    def export_to_file(collection_dict: Dict[str, Any], file_path: Path) -> None:
        """Export collection to Insomnia file.

        Args:
            collection_dict: Collection dict
            file_path: Path to save file
        """
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(collection_dict, f, indent=2, ensure_ascii=False)

