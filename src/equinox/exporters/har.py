"""HAR (HTTP Archive) exporter."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from equinox import __version__ as VERSION
from equinox.security import redact_headers
from equinox.core.request import Request, Response
from equinox.core.time import to_iso_z
from equinox.importers._utils import write_json_file
from equinox.storage.utils import coerce_body_to_str

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
        started_datetime: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Build a single HAR entry from *request* and *response*.

        Sensitive header values are redacted before being embedded.

        Args:
            request:          The outgoing HTTP request.
            response:         The received HTTP response.
            started_datetime: When the request was sent (defaults to now).

        Returns:
            A HAR entry dict conforming to the HTTP Archive 1.2 spec.
        """
        safe_req_headers  = redact_headers(request.headers or {})
        safe_resp_headers = redact_headers(dict(response.headers) if response.headers else {})
        resp_body         = coerce_body_to_str(response.body) if response.body else ""
        req_content_type  = (request.headers or {}).get(
            "Content-Type", "application/x-www-form-urlencoded"
        )
        elapsed_ms = int(response.elapsed * 1000) if response.elapsed else 0

        return {
            "startedDateTime": to_iso_z(started_datetime),
            "time":            elapsed_ms,
            "request": {
                "method":      request.method,
                "url":         request.url,
                "httpVersion": "HTTP/1.1",
                "headers":     [{"name": k, "value": v} for k, v in safe_req_headers.items()],
                "queryString": [
                    {"name": k, "value": v}
                    for k, v in (request.params or {}).items()
                ],
                "postData": (
                    {"mimeType": req_content_type, "text": request.body or ""}
                    if request.body else None
                ),
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
                    "mimeType": (response.headers or {}).get(
                        "Content-Type", "application/octet-stream"
                    ),
                    "text": resp_body,
                },
                "redirectURL": (
                    (response.headers or {}).get("Location", "")
                    if response.headers else ""
                ),
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
                "creator": {"name": "Equinox", "version": VERSION},
                "entries": entries,
            }
        }

    @staticmethod
    def export_to_file(har_dict: Dict[str, Any], file_path: Path) -> None:
        """Write *har_dict* as pretty-printed JSON to *file_path*.

        Args:
            har_dict:  Complete HAR document dict.
            file_path: Destination; parent directories are created if absent.

        Raises:
            IOError: If the file cannot be written.
        """
        write_json_file(har_dict, file_path)

