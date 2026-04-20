"""HTTP Request dataclass and related helpers."""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from equinox.core import urls
from equinox.core.exceptions import ValidationError
from equinox.core.redact import redact_url
from equinox.core.validation import VALID_HTTP_METHODS as VALID_METHODS
from equinox.core.request.types import (
    DEFAULT_METHOD,
    DEFAULT_TIMEOUT,
    AssertionRule,
    CaptureRule,
    MultipartField,
)
from equinox.core.request.headers import HeaderDict

__all__ = ["Request"]

logger = logging.getLogger(__name__)


# ── Private helpers ───────────────────────────────────────────────────────────


def _decode_body(body: Optional[Union[str, bytes]], encoding: str = "utf-8") -> str:
    """Decode *body* to ``str``, handling both ``str`` and ``bytes`` input.

    Args:
        body:     Body content — ``str``, ``bytes``, or ``None``.
        encoding: Charset for bytes decoding (default UTF-8).

    Returns:
        Decoded string, or ``""`` when *body* is ``None``.
    """
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode(encoding)
    return body


def _short(s: str, max_len: int = 60) -> str:
    """Truncate *s* to *max_len* chars, appending ``"…"`` when truncated."""
    return s if len(s) <= max_len else s[:max_len] + "…"


def _is_template_url(url: str) -> bool:
    """Return ``True`` when *url* still contains ``{{VAR}}`` placeholders."""
    return "{{" in url


def _is_absolute_url(url: str) -> bool:
    """Return ``True`` when *url* has both a scheme and a netloc."""
    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


def _merge_query_params(url: str, params: Dict[str, str]) -> str:
    """Merge *params* into *url*, overriding any colliding existing keys.

    Args:
        url:    Absolute URL (must already have a scheme and netloc).
        params: Query parameters to add/merge.

    Returns:
        URL with merged and properly percent-encoded query string.
    """
    parsed = urlparse(url)
    merged = dict(parse_qsl(parsed.query, keep_blank_values=True))
    merged.update({str(k): str(v) for k, v in params.items()})
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        urlencode(merged, doseq=False),
        parsed.fragment,
    ))


# ── Request dataclass ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class Request:
    """HTTP request model.

    Construction is deliberately lenient — fields are validated at
    send-time by :class:`~equinox.core.validation.Validator` rather than
    at construction time.  This allows ``Request`` objects to be freely
    edited, imported, or partially populated by the GUI before they are sent.
    """

    method: str
    url: str

    headers: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, str] = field(default_factory=dict)
    body: Optional[Union[str, bytes]] = None

    auth: Optional[Any] = None

    timeout: float = field(default=DEFAULT_TIMEOUT)
    follow_redirects: bool = True
    verify_ssl: bool = True

    # ── Metadata ──────────────────────────────────────────────────────────────
    name: Optional[str] = None
    description: Optional[str] = None
    collection_id: Optional[int] = None
    folder: Optional[str] = None
    id: Optional[int] = None

    # ── Advanced features ─────────────────────────────────────────────────────
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
            raise ValidationError(f"Invalid HTTP method: {self.method!r}")
        self.headers = HeaderDict(self.headers or {})
        safe_url = _short(redact_url(self.url))
        logger.debug(
            "Request initialised: %s %s id=%s collection=%s",
            self.method, safe_url, self.id, self.collection_id,
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict (suitable for JSON/storage)."""
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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Request":
        """Deserialise from a plain dict produced by :meth:`to_dict`."""
        request = cls(
            method=data.get("method", DEFAULT_METHOD),
            url=data["url"],
            headers=data.get("headers", {}),
            params=data.get("params", {}),
            body=data.get("body"),
            timeout=data.get("timeout", DEFAULT_TIMEOUT),
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

    # ── URL helpers ───────────────────────────────────────────────────────────

    def _final_url(self) -> str:
        """Return the fully resolved URL with path params expanded and query params merged."""
        url = urls.expand_placeholders(self.url, self.path_params or None)
        if not self.params:
            return url
        # URL still contains unresolved template vars or is relative —
        # append params with safe percent-encoding.
        if _is_template_url(url) or not _is_absolute_url(url):
            qs = urlencode(self.params)
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}{qs}"
        return _merge_query_params(url, self.params)

    # ── cURL export ───────────────────────────────────────────────────────────

    def to_curl(self) -> str:
        """Return a ``curl`` command string equivalent to this request."""
        parts = ["curl"]
        if self.method != DEFAULT_METHOD:
            parts.extend(["-X", self.method])
        for k, v in self.headers.items():
            parts.extend(["-H", f"{k}: {v}"])
        if self.body:
            parts.extend(["-d", _decode_body(self.body)])
        parts.append(self._final_url())
        return shlex.join(parts)

