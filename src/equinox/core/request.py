"""Request and Response models"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List
from datetime import datetime, timezone
import json

from equinox.core import utc_now


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

    def to_dict(self) -> Dict[str, Any]:
        """Convert request to dictionary"""
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
            d["auth"] = self.auth.to_dict()
            d["auth_type"] = type(self.auth).__name__
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Request":
        """Create request from dictionary"""
        return cls(
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

    def to_curl(self) -> str:
        """Convert request to curl command"""
        import shlex

        parts = ["curl"]

        # Method
        if self.method != "GET":
            parts.extend(["-X", self.method])

        # Headers — use shlex.quote to safely handle values with special chars
        for key, value in self.headers.items():
            parts.extend(["-H", f"{key}: {value}"])

        # Body — pass via shlex.quote to handle embedded quotes/special chars
        if self.body:
            parts.extend(["-d", self.body])

        # URL with params
        url = self.url
        if self.params:
            if "{{" in url or (not url.startswith("http://") and not url.startswith("https://")):
                # Template URL — can't parse with urlps yet, use string concat
                qs = "&".join(f"{k}={v}" for k, v in self.params.items())
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}{qs}"
            else:
                from urlps import parse_url_unsafe
                u = parse_url_unsafe(url)
                for k, v in self.params.items():
                    u = u.with_query_param(str(k), str(v))
                url = str(u)

        parts.append(url)

        return shlex.join(parts)


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

    def __post_init__(self):
        """Ensure headers are case-insensitive"""
        self.headers = {k.lower(): v for k, v in self.headers.items()}

    @property
    def text(self) -> str:
        """Get response body as text"""
        encoding = self.encoding or "utf-8"
        return self.body.decode(encoding, errors="replace")

    @property
    def encoding(self) -> Optional[str]:
        """Extract encoding from content-type header"""
        content_type = self.headers.get("content-type", "")
        if not content_type or "charset" not in content_type:
            return None
        from email.message import Message
        msg = Message()
        msg["content-type"] = content_type
        return msg.get_param("charset")

    def json(self) -> Any:
        """Parse response body as JSON"""
        return json.loads(self.text)

    @property
    def content_type(self) -> Optional[str]:
        """Get content type"""
        ct = self.headers.get("content-type", "")
        return ct.split(";")[0].strip() if ct else None

    @property
    def is_json(self) -> bool:
        """Check if response is JSON"""
        ct = self.content_type
        return ct is not None and "json" in ct

    @property
    def is_html(self) -> bool:
        """Check if response is HTML"""
        ct = self.content_type
        return ct is not None and "html" in ct

    @property
    def is_xml(self) -> bool:
        """Check if response is XML"""
        ct = self.content_type
        return ct is not None and "xml" in ct

    @property
    def size(self) -> int:
        """Get response size in bytes"""
        return len(self.body)

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary"""
        return {
            "status_code": self.status_code,
            "reason": self.reason,
            "headers": dict(self.headers),
            "body": self.text,
            "elapsed": self.elapsed,
            "timestamp": self.timestamp.isoformat(),
            "content_type": self.content_type,
            "size": self.size,
        }
