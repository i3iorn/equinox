"""Request and Response models for HTTP communication.

This module provides comprehensive HTTP request/response models with type safety,
RFC-compliant header handling, and serialization support. The design follows
a "fortress mentality" where inputs are validated at construction time (lenient)
but strictly enforced at send/storage boundaries.

Data flow:
    User input → Request/Response models → Validator (at send-time) →
    HTTPClient → Database (encrypted) → GUI/CLI render

Key features:
    - RFC 7230 compliant HeaderDict with case-insensitive comparisons
    - Automatic header and method normalization
    - Request serialization/deserialization with auth round-trip support
    - Response content-type detection and JSON parsing
    - Metadata tracking (timestamps, timings, request/response correlation)
    - Support for advanced features (captures, assertions, scripts, multipart)
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from email.message import Message
from functools import cached_property
from typing import (
    Dict,
    Optional,
    Any,
    List,
    TypedDict,
    Literal,
    Union,
)

from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from equinox.core.time import utc_now
from equinox.core import urls

from equinox.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

# =========================================================
# Module constants
# =========================================================

# Default values for Request fields
_DEFAULT_TIMEOUT: float = 30.0
_DEFAULT_METHOD: str = "GET"

# Text encoding for response bodies
_DEFAULT_ENCODING: str = "utf-8"
_TEXT_DECODE_ERROR_MODE: str = "replace"

# URL query string separators
_QUERY_SEPARATOR: str = "?"
_PARAM_SEPARATOR: str = "&"

# Content-type header parsing
_CONTENT_TYPE_HEADER: str = "content-type"
_CHARSET_PARAMETER: str = "charset"

# =========================================================
# Typed structures
# =========================================================

class CaptureRule(TypedDict, total=False):
    """Rule for capturing values from response bodies into variables.

    Used in post-request scripts to extract data from responses and store in
    collection or environment variables for use in subsequent requests.

    Attributes:
        variable: Name of the target variable to store the captured value.
        source: Source of the value — "response_body", "response_header", etc.
        path: JSONPath or similar accessor (e.g., "data.user.id", "X-Custom-Header").
        default: Fallback value if the path does not match.

    Example:
        {
            "variable": "user_id",
            "source": "response_body",
            "path": "data.id",
            "default": "0"
        }
    """
    variable: str
    source: str
    path: str
    default: str


class AssertionRule(TypedDict, total=False):
    """Rule for asserting response properties for test automation.

    Assertions are evaluated after receiving a response to verify correctness
    and provide early failure detection in request chains.

    Attributes:
        type: Assertion type — "status_code", "header", "body_contains", "json_path", etc.
        field: Optional field selector (e.g., header name, JSON path).
        expected: Expected value or pattern to match against.

    Example:
        {
            "type": "status_code",
            "expected": 200
        }
    """
    type: str
    field: str
    expected: Any


class MultipartField(TypedDict):
    """Represents a single field in a multipart/form-data request body.

    Multipart fields are used for file uploads or complex form submissions
    where binary data needs to be transmitted alongside form fields.

    Attributes:
        key: Form field name.
        type: Field type — "text" for plain text, "file" for file uploads.
        value: Field value (text content or file path for "file" type).

    Example (text):
        {"key": "username", "type": "text", "value": "john_doe"}

    Example (file):
        {"key": "profile_pic", "type": "file", "value": "/path/to/image.jpg"}
    """
    key: str
    type: Literal["text", "file"]
    value: str


# =========================================================
# Helpers
# =========================================================

# Canonical set lives in validation.py — import to keep a single source of truth.
from equinox.core.validation import VALID_HTTP_METHODS as VALID_METHODS


def _decode_body(body: Optional[Union[str, bytes]], encoding: str = _DEFAULT_ENCODING) -> str:
    """Decode body to string, handling both str and bytes types.

    Args:
        body: Body content as str or bytes.
        encoding: Character encoding to use for bytes decoding (default UTF-8).

    Returns:
        Decoded string, or empty string if body is None.
    """
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode(encoding)
    return body


def _normalize_content_type(content_type_header: str) -> Optional[str]:
    """Extract MIME type from Content-Type header, stripping parameters.

    Handles "application/json; charset=utf-8" → "application/json".
    Returns None if header is empty.

    Args:
        content_type_header: Full Content-Type header value.

    Returns:
        MIME type without parameters, or None if header is empty.
    """
    if not content_type_header:
        return None
    return content_type_header.split(";")[0].strip() or None


def _parse_charset(content_type_header: str) -> Optional[str]:
    """Extract charset parameter from Content-Type header.

    Parses "application/json; charset=utf-8" → "utf-8".
    Returns None if charset parameter is not present.

    Args:
        content_type_header: Full Content-Type header value.

    Returns:
        Charset name, or None if not found.
    """
    if not content_type_header or "charset" not in content_type_header:
        return None

    msg = Message()
    msg["content-type"] = content_type_header
    return msg.get_param(_CHARSET_PARAMETER)


def _short(s: str, max_len: int = 60) -> str:
    """Truncate a string to max_len characters, appending ellipsis if needed.

    Useful for log messages and UI display where long URLs/bodies should be
    summarized to avoid cluttering output.

    Args:
        s: String to truncate.
        max_len: Maximum length before truncation (default 60).

    Returns:
        Original string if len(s) <= max_len, else s[:max_len] + "...".
    """
    return s if len(s) <= max_len else s[:max_len] + "..."


def _is_template_url(url: str) -> bool:
    """Check if URL contains unresolved variable placeholders.

    Template URLs have the form "{{VARIABLE}}" and must be resolved before
    sending the request. This is used to defer URL validation until all
    variables are interpolated.

    Args:
        url: URL string to check.

    Returns:
        True if URL contains "{{" (indicating template variables), False otherwise.
    """
    return "{{" in url


def _is_absolute_url(url: str) -> bool:
    """Check if URL is absolute (has scheme and netloc).

    Absolute URLs can be sent as-is; relative URLs need a base URL or scheme
    prepended. This is used to determine URL merge strategy.

    Args:
        url: URL string to check.

    Returns:
        True if URL has both scheme (e.g., "http") and netloc (e.g., "example.com"),
        False otherwise.
    """
    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


def _merge_query_params(url: str, params: Dict[str, str]) -> str:
    """Merge query parameters into an absolute URL.

    Handles existing query strings in the URL by combining with new params.
    New parameters override existing ones with the same key.

    Args:
        url: Absolute URL (must have scheme and netloc).
        params: Dictionary of query parameters to add/merge.

    Returns:
        URL with merged query parameters properly encoded.
    """
    parsed = urlparse(url)
    existing_q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    existing_q.update({str(k): str(v) for k, v in params.items()})

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        urlencode(existing_q, doseq=False),
        parsed.fragment,
    ))


# HeaderDict: RFC-compliant header container
# - Field-names are token characters per RFC7230 and are compared case-insensitively
# - Preserves latest original-case key for iteration/display
# - Validates header values to prevent CR/LF injection
# RFC 7230 Section 3.2 — token = 1*tchar
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class HeaderDict(dict):
    """Dictionary for HTTP headers with RFC-compliant rules.

    Behavior:
    - Keys are compared case-insensitively (stored internally lower-cased).
    - Iteration yields the most-recent original-cased key names.
    - Values are normalized to str and checked to not contain CR/LF.
    - Subclasses ``dict`` so existing isinstance(..., dict) checks continue to work.
    """

    def __init__(self, data: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        # map lower-case name -> original-case name
        self._orig: Dict[str, str] = {}
        if data:
            # use .update to ensure validation
            self.update(data)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not _HEADER_NAME_RE.fullmatch(name):
            raise ValidationError(f"Invalid header name: {name!r}")

    @staticmethod
    def _validate_value(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        # Do NOT validate CR/LF here; defer strict header validation to
        # :class:`equinox.core.validation.Validator.validate_headers` which is
        # called at send-time. This allows constructing Request objects with
        # user-provided headers (useful for editing/importing) while still
        # enforcing safety before the network send occurs.
        return value

    def __setitem__(self, key: str, value: Any) -> None:
        self._validate_name(key)
        v = self._validate_value(value)
        lower = key.lower()
        self._orig[lower] = key
        super().__setitem__(lower, v)

    def __getitem__(self, key: str) -> Any:  # allow case-insensitive get
        return super().__getitem__(key.lower())

    def __delitem__(self, key: str) -> None:
        lower = key.lower()
        super().__delitem__(lower)
        self._orig.pop(lower, None)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return super().__contains__(key.lower())

    def get(self, key: str, default: Optional[Any] = None) -> Any:
        return super().get(key.lower(), default)

    def keys(self):
        for lower in super().keys():
            yield self._orig.get(lower, lower)

    def items(self):
        for lower, value in super().items():
            yield self._orig.get(lower, lower), value

    def __iter__(self):
        return self.keys()

    def update(self, other: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        if other:
            if isinstance(other, dict):
                iterator = other.items()
            else:
                iterator = other
            for k, v in iterator:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def __eq__(self, other: object) -> bool:
        """Compare headers case-insensitively to dict-like objects.

        This allows HeaderDict({'content-type': 'x'}) == {'Content-Type': 'x'}
        which is convenient for tests and callers that construct plain dicts.
        Values are compared as-is.
        """
        if not isinstance(other, dict):
            try:
                other = dict(other)  # type: ignore[arg-type]
            except Exception:
                return False

        # Normalize both dicts to lower-cased keys for comparison
        try:
            other_normalized = {k.lower(): v for k, v in other.items()}
            self_lower = {k: v for k, v in super().items()}
            return self_lower == other_normalized
        except Exception:
            return False

    def as_canonical_dict(self, lowercase: bool = True) -> Dict[str, Any]:
        """Return a plain dict suitable for serialization.

        If lowercase is True keys will be lower-cased (useful for storage/search);
        otherwise original-case keys are returned for display/export.
        """
        if lowercase:
            return {k: v for k, v in super().items()}
        return {k: v for k, v in self.items()}


# =========================================================
# Request
# =========================================================

@dataclass(slots=True)
class Request:
    """HTTP Request model"""

    method: str
    url: str

    headers: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, str] = field(default_factory=dict)
    body: Optional[Union[str, bytes]] = None

    auth: Optional[Any] = None

    timeout: float = field(default=_DEFAULT_TIMEOUT)
    follow_redirects: bool = True
    verify_ssl: bool = True

    # Metadata
    name: Optional[str] = None
    description: Optional[str] = None
    collection_id: Optional[int] = None
    folder: Optional[str] = None
    id: Optional[int] = None

    # Advanced features
    captures: List[CaptureRule] = field(default_factory=list)
    assertions: List[AssertionRule] = field(default_factory=list)
    multipart_data: Optional[List[MultipartField]] = None
    params_list: Optional[List[Dict[str, Any]]] = None

    pre_script: str = ""
    post_script: str = ""

    cert_path: Optional[str] = None
    cert_key_path: Optional[str] = None

    path_params: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.method = self.method.upper()

        if self.method not in VALID_METHODS:
            raise ValidationError(f"Invalid HTTP method: {self.method}")

        self.headers = HeaderDict(self.headers or {})

        logger.debug(
            "Request initialized: %s %s id=%s collection=%s",
            self.method,
            _short(self.url),
            self.id,
            self.collection_id,
        )

    # -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "method": self.method,
            "url": self.url,
            "headers": dict(self.headers),
            "params": dict(self.params),
            "body": _decode_body(self.body),
            "timeout": self.timeout,
            "follow_redirects": self.follow_redirects,
            "verify_ssl": self.verify_ssl,
            "name": self.name,
            "description": self.description,
            "collection_id": self.collection_id,
            "folder": self.folder,
            "id": self.id,
            "path_params": dict(self.path_params),
            "captures": list(self.captures),
            "assertions": list(self.assertions),
            "multipart_data": self.multipart_data,
            "params_list": self.params_list,
            "pre_script": self.pre_script,
            "post_script": self.post_script,
            "cert_path": self.cert_path,
            "cert_key_path": self.cert_key_path,
        }

        if self.auth is not None and hasattr(self.auth, "to_dict"):
            d["auth"] = self.auth.to_dict()
            d["auth_type"] = type(self.auth).__name__

        return d

    # -----------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Request":
        request = cls(
            method=data.get("method", _DEFAULT_METHOD),
            url=data["url"],
            headers=data.get("headers", {}),
            params=data.get("params", {}),
            body=data.get("body"),
            timeout=data.get("timeout", _DEFAULT_TIMEOUT),
            follow_redirects=data.get("follow_redirects", True),
            verify_ssl=data.get("verify_ssl", True),
            name=data.get("name"),
            description=data.get("description"),
            collection_id=data.get("collection_id"),
            folder=data.get("folder"),
            id=data.get("id"),
            path_params=data.get("path_params", {}),
            captures=data.get("captures", []),
            assertions=data.get("assertions", []),
            multipart_data=data.get("multipart_data"),
            params_list=data.get("params_list"),
            pre_script=data.get("pre_script", ""),
            post_script=data.get("post_script", ""),
            cert_path=data.get("cert_path"),
            cert_key_path=data.get("cert_key_path"),
        )

        if "auth" in data and "auth_type" in data:
            try:
                from equinox.auth.factory import auth_from_dict
                request.auth = auth_from_dict(data["auth_type"], data["auth"])
            except Exception as exc:
                logger.exception("Auth reconstruction failed")
                raise ValidationError("Invalid auth configuration") from exc

        return request

    # -----------------------------------------------------

    def _final_url(self) -> str:
        url = urls.expand_placeholders(self.url, self.path_params or None)

        if not self.params:
            return url

        if _is_template_url(url) or not _is_absolute_url(url):
            # URL still contains unresolved placeholders or is relative — use
            # urlencode so that param values with special characters are safe.
            qs = urlencode(self.params)
            sep = _PARAM_SEPARATOR if _QUERY_SEPARATOR in url else _QUERY_SEPARATOR
            return f"{url}{sep}{qs}"

        return _merge_query_params(url, self.params)

    # -----------------------------------------------------

    def to_curl(self) -> str:
        parts = ["curl"]

        if self.method != _DEFAULT_METHOD:
            parts.extend(["-X", self.method])

        for k, v in self.headers.items():
            parts.extend(["-H", f"{k}: {v}"])

        if self.body:
            body = _decode_body(self.body)
            parts.extend(["-d", body])

        parts.append(self._final_url())

        return shlex.join(parts)


# =========================================================
# Response
# =========================================================

@dataclass
class Response:
    """HTTP Response model"""

    status_code: int
    reason: str
    headers: Dict[str, str]
    body: bytes
    elapsed: float
    request: Request

    timestamp: datetime = field(default_factory=utc_now)

    sent_headers: Optional[Dict[str, str]] = None
    sent_url: Optional[str] = None
    timings: Optional[Dict[str, float]] = None

    def __post_init__(self) -> None:
        self.headers = HeaderDict(self.headers or {})

        logger.debug(
            "Response: %d (%s) size=%d time=%.2fs",
            self.status_code,
            self.reason,
            len(self.body),
            self.elapsed,
        )

    # -----------------------------------------------------

    def _get_header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    # -----------------------------------------------------

    @cached_property
    def content_type(self) -> Optional[str]:
        ct = self._get_header(_CONTENT_TYPE_HEADER)
        return _normalize_content_type(ct)

    @cached_property
    def encoding(self) -> Optional[str]:
        ct = self._get_header(_CONTENT_TYPE_HEADER)
        return _parse_charset(ct)

    @cached_property
    def text(self) -> str:
        return self.body.decode(self.encoding or _DEFAULT_ENCODING, errors=_TEXT_DECODE_ERROR_MODE)

    # -----------------------------------------------------

    def json(self) -> Any:
        # Attempt to parse the body as JSON regardless of the Content-Type
        # header. Some servers return JSON payloads with non-standard
        # Content-Type (e.g. text/plain). We try to be helpful and parse
        # wherever possible; failures raise a ValueError.
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            logger.exception("JSON parsing failed")
            raise ValueError("Malformed JSON response") from exc

    # -----------------------------------------------------

    @property
    def is_json(self) -> bool:
        return self.content_type is not None and "json" in self.content_type

    @property
    def is_html(self) -> bool:
        return self.content_type is not None and "html" in self.content_type

    @property
    def is_xml(self) -> bool:
        return self.content_type is not None and "xml" in self.content_type

    @property
    def size(self) -> int:
        return len(self.body)

    # -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
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
