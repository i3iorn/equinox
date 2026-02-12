"""Request and Response models"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List
from datetime import datetime
import json


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

    def to_dict(self) -> Dict[str, Any]:
        """Convert request to dictionary"""
        return {
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
        }

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
        )

    def to_curl(self) -> str:
        """Convert request to curl command"""
        parts = ["curl"]

        # Method
        if self.method != "GET":
            parts.append(f"-X {self.method}")

        # Headers
        for key, value in self.headers.items():
            parts.append(f'-H "{key}: {value}"')

        # Body
        if self.body:
            parts.append(f"-d '{self.body}'")

        # URL with params
        url = self.url
        if self.params:
            param_str = "&".join(f"{k}={v}" for k, v in self.params.items())
            url = f"{url}?{param_str}"

        parts.append(f'"{url}"')

        return " ".join(parts)


@dataclass
class Response:
    """HTTP Response model"""

    status_code: int
    reason: str
    headers: Dict[str, str]
    body: bytes
    elapsed: float  # Response time in seconds
    request: Request
    timestamp: datetime = field(default_factory=datetime.now)

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
        if "charset=" in content_type:
            return content_type.split("charset=")[1].split(";")[0].strip()
        return None

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
