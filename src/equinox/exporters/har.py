"""HAR (HTTP Archive) exporter."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from equinox.core.request import Request, Response
from equinox.core.util.time import to_iso_z
from equinox.importers._utils import write_json_file
from equinox.security import redact_headers
from equinox.storage.utils import coerce_body_to_str
from equinox.versioning import get_app_version

__all__ = ["HARExporter"]

logger = logging.getLogger(__name__)


class HARExporter:
    """Export request/response pairs in HAR (HTTP Archive 1.2) format.

    Usage::

        entry  = HARExporter.export_request_response(request, response)
        archive = HARExporter.create_har_archive([entry])
        HARExporter.export_to_file(archive, Path("trace.har"))
    """

    @staticmethod
    def export_request_response(
        request: Request,
        response: Response,
        started_datetime: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a HAR entry representing a request/response pair.

        Args:
            request: Outgoing HTTP request.
            response: Incoming HTTP response.
            started_datetime: Timestamp when request was sent.

        Returns:
            HAR entry dictionary.
        """
        HARExporter._validate_request(request)
        HARExporter._validate_response(response)

        safe_req_headers = HARExporter._safe_headers(request.headers)
        safe_resp_headers = HARExporter._safe_headers(response.headers)

        resp_body = HARExporter._safe_body(response.body)
        req_content_type = HARExporter._request_content_type(request.headers)
        elapsed_ms = HARExporter._elapsed_ms(response.elapsed)

        return {
            "startedDateTime": to_iso_z(started_datetime),
            "time": elapsed_ms,
            "request": HARExporter._build_request_block(
                request=request,
                safe_headers=safe_req_headers,
                req_content_type=req_content_type,
            ),
            "response": HARExporter._build_response_block(
                response=response,
                safe_headers=safe_resp_headers,
                resp_body=resp_body,
            ),
            "cache": {},
            "timings": HARExporter._build_timings_block(elapsed_ms),
        }

    @staticmethod
    def _validate_request(request: Request) -> None:
        if not isinstance(request, Request):
            raise TypeError("request must be a Request instance")
        if not isinstance(request.method, str) or not request.method:
            raise ValueError("request.method must be a non-empty string")
        if not isinstance(request.url, str) or not request.url:
            raise ValueError("request.url must be a non-empty string")

    @staticmethod
    def _validate_response(response: Response) -> None:
        if not isinstance(response, Response):
            raise TypeError("response must be a Response instance")
        if not isinstance(response.status_code, int):
            raise ValueError("response.status_code must be an integer")

    @staticmethod
    def _safe_headers(headers: dict[str, str] | None) -> dict[str, str]:
        redacted = redact_headers(headers or {})
        return {str(key): "" if value is None else str(value) for key, value in redacted.items()}

    @staticmethod
    def _safe_body(body: Any) -> str:
        if not body:
            return ""
        coerced = coerce_body_to_str(body)
        return coerced if coerced is not None else ""

    @staticmethod
    def _request_content_type(headers: dict[str, str] | None) -> str:
        if not isinstance(headers, dict):
            return "application/x-www-form-urlencoded"
        value = headers.get("Content-Type")
        if isinstance(value, str) and value:
            return value
        return "application/x-www-form-urlencoded"

    @staticmethod
    def _elapsed_ms(elapsed: float | None) -> int:
        return int(elapsed * 1000) if isinstance(elapsed, (int, float)) else 0

    @staticmethod
    def _build_request_block(
        request: Request,
        safe_headers: dict[str, str],
        req_content_type: str,
    ) -> dict[str, Any]:
        return {
            "method": request.method,
            "url": request.url,
            "httpVersion": "HTTP/1.1",
            "headers": [{"name": k, "value": v} for k, v in safe_headers.items()],
            "queryString": [{"name": k, "value": v} for k, v in (request.params or {}).items()],
            "postData": (
                {"mimeType": req_content_type, "text": request.body or ""} if request.body else None
            ),
            "cookies": [],
            "headersSize": HARExporter._headers_size(safe_headers),
            "bodySize": len(request.body) if request.body else 0,
        }

    @staticmethod
    def _build_response_block(
        response: Response,
        safe_headers: dict[str, str],
        resp_body: str,
    ) -> dict[str, Any]:
        return {
            "status": response.status_code,
            "statusText": response.reason or "",
            "httpVersion": "HTTP/1.1",
            "headers": [{"name": k, "value": v} for k, v in safe_headers.items()],
            "cookies": [],
            "content": {
                "size": len(response.body) if response.body else 0,
                "mimeType": (response.headers or {}).get(
                    "Content-Type",
                    "application/octet-stream",
                ),
                "text": resp_body,
            },
            "redirectURL": (response.headers or {}).get("Location", ""),
            "headersSize": HARExporter._headers_size(safe_headers),
            "bodySize": len(response.body) if response.body else 0,
        }

    @staticmethod
    def _build_timings_block(wait_ms: int) -> dict[str, int]:
        return {
            "blocked": -1,
            "dns": -1,
            "connect": -1,
            "send": -1,
            "wait": wait_ms,
            "receive": -1,
            "ssl": -1,
        }

    @staticmethod
    def _headers_size(headers: dict[str, str]) -> int:
        return sum(len(f"{k}: {v}\r\n") for k, v in headers.items())

    @staticmethod
    def create_har_archive(
        entries: list[dict[str, Any]],
        title: str = "Equinox Archive",
    ) -> dict[str, Any]:
        """Wrap *entries* in a standard HAR log envelope.

        Args:
            entries: List of HAR entry dicts (from
                     :meth:`export_request_response`).
            title:   Archive title embedded in the ``creator`` field.

        Returns:
            A complete HAR document dict.
        """
        return {
            "log": {
                "version": "1.2",
                "creator": {"name": "Equinox", "version": get_app_version()},
                "entries": entries,
            },
        }

    @staticmethod
    def export_to_file(har_dict: dict[str, Any], file_path: Path) -> None:
        """Write *har_dict* as pretty-printed JSON to *file_path*.

        Args:
            har_dict:  Complete HAR document dict.
            file_path: Destination; parent directories are created if absent.

        Raises:
            IOError: If the file cannot be written.
        """
        write_json_file(har_dict, file_path)
