"""Request and Response models"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List
from datetime import datetime
import json

from equinox.core.time import utc_now
from equinox.core import urls

logger = logging.getLogger(__name__)


@dataclass
class Request:
    """HTTP Request model"""

    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, str] = field(default_factory=dict)
    body: Optional[str] = None
    auth: Optional[Any] = None
    timeout: float = 30.0
    follow_redirects: bool = True
    verify_ssl: bool = True

    # Metadata
    name: Optional[str] = None
    description: Optional[str] = None
    collection_id: Optional[int] = None
    folder: Optional[str] = None
    id: Optional[int] = None  # set when loaded from DB; used for autosave

    # Capture rules: list of dicts with keys variable/source/path/default
    captures: List[Any] = field(default_factory=list)

    # Scripts (Python stdlib only)
    pre_script:  str = ""
    post_script: str = ""

    # Client certificate paths (PEM)
    cert_path:     Optional[str] = None
    cert_key_path: Optional[str] = None

    # Multipart form-data fields: list of {"key", "type": "text"|"file", "value"}
    multipart_data: Optional[List[Any]] = None

    # Test assertion rules: list of {"type", "field", "expected"}
    assertions: List[Any] = field(default_factory=list)

    # Full params list with per-row enabled flag:
    # [{"key": str, "value": str, "enabled": bool}, ...]
    # When set, this is the authoritative source; `params` holds only enabled rows.
    params_list: Optional[List[Any]] = None

    # Path parameter values: {"id": "123", "postId": "456"}
    # Extracted from {{param}} tokens in the URL.  Values are merged into
    # the variable dict at send time so interpolation replaces the tokens.
    path_params: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Log request initialization"""
        logger.debug(
            "Request.__post_init__(): method=%s url=%s id=%s collection=%s auth=%s",
            self.method,
            self.url[:60] + "..." if len(self.url) > 60 else self.url,
            self.id,
            self.collection_id,
            type(self.auth).__name__ if self.auth else "None",
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert request to dictionary"""
        logger.debug(
            "Request.to_dict(): method=%s url=%s headers=%d params=%d auth=%s",
            self.method,
            self.url[:60] + "..." if len(self.url) > 60 else self.url,
            len(self.headers),
            len(self.params),
            type(self.auth).__name__ if self.auth else "None",
        )
        d: Dict[str, Any] = {
            "method": self.method,
            "url": self.url,
            "headers": self.headers,
            "params": self.params,
            "body": self.body,
            "timeout": self.timeout,
            "follow_redirects": self.follow_redirects,
            "verify_ssl": self.verify_ssl,
            "name": self.name,
            "description": self.description,
            "collection_id": self.collection_id,
            "folder": self.folder,
            "id": self.id,
            "path_params": self.path_params,
            "captures": [c if isinstance(c, dict) else c for c in self.captures],
            "pre_script": self.pre_script,
            "post_script": self.post_script,
            "cert_path": self.cert_path,
            "cert_key_path": self.cert_key_path,
            "multipart_data": self.multipart_data,
            "assertions": self.assertions,
            "params_list": self.params_list,
        }
        if self.auth is not None and hasattr(self.auth, "to_dict"):
            logger.debug("Serializing auth: type=%s", type(self.auth).__name__)
            d["auth"] = self.auth.to_dict()
            d["auth_type"] = type(self.auth).__name__
        logger.debug("Request.to_dict() completed: %d fields", len(d))
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Request":
        """Create request from dictionary"""
        logger.debug(
            "Request.from_dict(): method=%s url=%s headers=%d params=%d",
            data.get("method", "GET"),
            (data.get("url", "")[:60] + "...") if len(data.get("url", "")) > 60 else data.get("url", ""),
            len(data.get("headers", {})),
            len(data.get("params", {})),
        )
        
        request = cls(
            method=data.get("method", "GET"),
            url=data["url"],
            headers=data.get("headers", {}),
            params=data.get("params", {}),
            body=data.get("body"),
            timeout=data.get("timeout", 30.0),
            follow_redirects=data.get("follow_redirects", True),
            verify_ssl=data.get("verify_ssl", True),
            name=data.get("name"),
            description=data.get("description"),
            collection_id=data.get("collection_id"),
            folder=data.get("folder"),
            id=data.get("id"),
            path_params=data.get("path_params", {}),
            captures=data.get("captures", []),
            pre_script=data.get("pre_script", ""),
            post_script=data.get("post_script", ""),
            cert_path=data.get("cert_path"),
            cert_key_path=data.get("cert_key_path"),
            multipart_data=data.get("multipart_data"),
            assertions=data.get("assertions", []),
            params_list=data.get("params_list"),
        )
        
        if "auth" in data and "auth_type" in data:
            auth_type = data["auth_type"]
            logger.debug("Reconstructing auth from dict: type=%s", auth_type)
            try:
                # Delegate to the auth factory to keep this module focused on
                # the Request model (single responsibility) and avoid
                # duplicating the auth-type -> constructor mapping.
                from equinox.auth.factory import auth_from_dict

                request.auth = auth_from_dict(auth_type, data["auth"])
            except Exception as auth_exc:
                logger.error("Failed to reconstruct auth %s: %s", auth_type, auth_exc)
        
        logger.debug(
            "Request.from_dict() completed: id=%s collection=%s",
            request.id, request.collection_id,
        )
        return request

    def to_curl(self) -> str:
        """Convert request to curl command"""
        import shlex

        logger.debug(
            "Request.to_curl(): method=%s url=%s headers=%d params=%d",
            self.method, self.url[:60] + "..." if len(self.url) > 60 else self.url,
            len(self.headers), len(self.params),
        )

        parts = ["curl"]

        # Method
        if self.method != "GET":
            parts.extend(["-X", self.method])
            logger.debug("Adding HTTP method: %s", self.method)

        for key, value in self.headers.items():
            parts.extend(["-H", f"{key}: {value}"])
        logger.debug("Added %d headers to curl command", len(self.headers))

        if self.body:
            parts.extend(["-d", self.body])
            logger.debug("Added request body to curl command (length=%d)", len(self.body))

        # URL with params — expand placeholders first (if any path_params present)
        url = urls.expand_placeholders(self.url, self.path_params or None)
        if self.params:
            logger.debug("Encoding %d query parameters into URL", len(self.params))
            # If the URL still looks like a template or is non-absolute, fall back to string concatenation
            if "{{" in url or (not url.startswith("http://") and not url.startswith("https://")):
                qs = "&".join(f"{k}={v}" for k, v in self.params.items())
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}{qs}"
                logger.debug("Using string concatenation for template URL")
            else:
                # Use stdlib urllib to safely merge query params for non-template URLs
                from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
                parsed = urlparse(url)
                existing_q = dict(parse_qsl(parsed.query, keep_blank_values=True))
                existing_q.update({str(k): str(v) for k, v in self.params.items()})
                new_query = urlencode(existing_q, doseq=False)
                url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
                logger.debug("Using urllib to encode query parameters")

        parts.append(url)
        
        curl_cmd = shlex.join(parts)
        logger.debug("to_curl() completed: length=%d chars", len(curl_cmd))
        return curl_cmd


@dataclass
class Response:
    """HTTP Response model"""

    status_code: int
    reason: str
    headers: Dict[str, str]
    body: bytes
    elapsed: float  # Response time in seconds
    request: Request
    timestamp: datetime = field(default_factory=lambda: utc_now())
    # Actual headers/URL used when sending (after auth is applied, params encoded)
    sent_headers: Optional[Dict[str, str]] = None
    sent_url: Optional[str] = None
    # Per-phase timing breakdown (ms); populated by HTTPClient
    timings: Optional[Dict[str, float]] = None

    def __post_init__(self) -> None:
        """Log response initialization"""
        logger.debug(
            "Response.__post_init__(): status=%d size=%d elapsed=%.2fs headers=%d content_type=%s",
            self.status_code,
            self.size,
            self.elapsed,
            len(self.headers),
            self.content_type,
        )

    def _get_header(self, name: str, default: str = "") -> str:
        """Case-insensitive header lookup.

        HTTP headers are case-insensitive per RFC 7230, but the
        ``headers`` dict may store any casing depending on the source.
        """
        name_lower = name.lower()
        for k, v in self.headers.items():
            if k.lower() == name_lower:
                logger.debug("_get_header(%s) found: %s", name, v[:50] + "..." if len(v) > 50 else v)
                return v
        logger.debug("_get_header(%s) not found, returning default", name)
        return default

    @property
    def text(self) -> str:
        """Get response body as text"""
        encoding = self.encoding or "utf-8"
        logger.debug("Response.text: decoding %d bytes with %s", len(self.body), encoding)
        return self.body.decode(encoding, errors="replace")

    @property
    def encoding(self) -> Optional[str]:
        """Extract encoding from content-type header"""
        content_type = self._get_header("content-type")
        if not content_type or "charset" not in content_type:
            logger.debug("Response.encoding: no charset found in content-type: %s", content_type)
            return None
        from email.message import Message
        msg = Message()
        msg["content-type"] = content_type
        enc = msg.get_param("charset")
        logger.debug("Response.encoding: extracted %s from content-type", enc)
        return enc

    def json(self) -> Any:
        """Parse response body as JSON"""
        try:
            logger.debug("Response.json(): parsing %d bytes as JSON", len(self.body))
            result = json.loads(self.text)
            logger.debug("Response.json(): parsing succeeded")
            return result
        except json.JSONDecodeError as json_err:
            logger.error("Response.json(): JSON parsing failed: %s", json_err)
            raise

    @property
    def content_type(self) -> Optional[str]:
        """Get content type"""
        ct = self._get_header("content-type")
        result = ct.split(";")[0].strip() if ct else None
        logger.debug("Response.content_type: %s", result)
        return result

    @property
    def is_json(self) -> bool:
        """Check if response is JSON"""
        ct = self.content_type
        result = ct is not None and "json" in ct
        logger.debug("Response.is_json: %s (content_type=%s)", result, ct)
        return result

    @property
    def is_html(self) -> bool:
        """Check if response is HTML"""
        ct = self.content_type
        result = ct is not None and "html" in ct
        logger.debug("Response.is_html: %s (content_type=%s)", result, ct)
        return result

    @property
    def is_xml(self) -> bool:
        """Check if response is XML"""
        ct = self.content_type
        result = ct is not None and "xml" in ct
        logger.debug("Response.is_xml: %s (content_type=%s)", result, ct)
        return result

    @property
    def size(self) -> int:
        """Get response size in bytes"""
        size = len(self.body)
        logger.debug("Response.size: %d bytes", size)
        return size

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary"""
        logger.debug(
            "Response.to_dict(): status=%d size=%d headers=%d elapsed=%.2fs",
            self.status_code, self.size, len(self.headers), self.elapsed,
        )
        result = {
            "status_code": self.status_code,
            "reason": self.reason,
            "headers": dict(self.headers),
            "body": self.text,
            "elapsed": self.elapsed,
            "timestamp": self.timestamp.isoformat(),
            "content_type": self.content_type,
            "size": self.size,
        }
        logger.debug("Response.to_dict() completed: %d fields", len(result))
        return result
