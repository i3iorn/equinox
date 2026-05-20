"""HTTP Response dataclass and related helpers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from email.message import Message
from functools import cached_property
from typing import Any

from equinox.core.request.headers import HeaderDict
from equinox.core.request.request import Request
from equinox.core.request.types import (
    CHARSET_PARAMETER,
    CONTENT_TYPE_HEADER,
    DEFAULT_ENCODING,
    TEXT_DECODE_ERROR_MODE,
)
from equinox.core.util.time import utc_now

__all__ = ["Response"]

logger = logging.getLogger(__name__)


# ── Private helpers ───────────────────────────────────────────────────────────


def _normalize_content_type(header_value: str) -> str | None:
    """Extract bare MIME type from a Content-Type header value.

    ``"application/json; charset=utf-8"`` → ``"application/json"``.

    Returns ``None`` when *header_value* is empty.
    """
    if not header_value:
        return None
    return header_value.split(";")[0].strip() or None


def _parse_charset(header_value: str) -> str | None:
    """Extract the ``charset`` parameter from a Content-Type header.

    ``"application/json; charset=utf-8"`` → ``"utf-8"``.

    Returns ``None`` when no ``charset`` parameter is present.
    """
    if not header_value or CHARSET_PARAMETER not in header_value:
        return None
    msg = Message()
    msg["content-type"] = header_value
    return msg.get_param(CHARSET_PARAMETER)


# ── Response dataclass ────────────────────────────────────────────────────────


@dataclass
class Response:
    """HTTP response model.

    ``body`` is always ``bytes`` as received from the transport layer.
    Text decoding is available via the :attr:`text` cached property which
    honours the charset declared in the ``Content-Type`` header.
    """

    status_code: int
    reason: str
    headers: dict[str, str]
    body: bytes
    elapsed: float
    request: Request

    timestamp: datetime = field(default_factory=utc_now)

    # Optional diagnostics populated by the dispatcher.
    sent_headers: dict[str, str] | None = None
    sent_url: str | None = None
    timings: dict[str, float] | None = None
    connection_info: dict[str, Any] | None = None
    retry_summary: str | None = (
        None  # Human-readable summary of retries (e.g. "retried 2× after 429")
    )
    # Preserves repeated Set-Cookie header values; dict(headers) collapses them.
    set_cookie_headers: list[str] | None = None

    def __post_init__(self) -> None:
        self.headers = HeaderDict(self.headers or {})
        logger.debug(
            "Response: %d (%s) size=%d bytes elapsed=%.3fs body_type=%s",
            self.status_code,
            self.reason,
            len(self.body),
            self.elapsed,
            type(self.body).__name__,
        )
        if len(self.body) > 0:
            logger.debug(
                "Response body preview (first 200 chars): %r",
                self.body[:200] if isinstance(self.body, bytes) else str(self.body)[:200],
            )

    # ── Header helper ─────────────────────────────────────────────────────────

    def _get_header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    # ── Cached content-type properties ────────────────────────────────────────

    @cached_property
    def content_type(self) -> str | None:
        """Bare MIME type from ``Content-Type``, e.g. ``"application/json"``."""
        return _normalize_content_type(self._get_header(CONTENT_TYPE_HEADER))

    @cached_property
    def encoding(self) -> str | None:
        """Charset declared by the ``Content-Type`` header, or ``None``."""
        return _parse_charset(self._get_header(CONTENT_TYPE_HEADER))

    @cached_property
    def text(self) -> str:
        """Response body decoded to ``str`` using the declared or default charset."""
        return self.body.decode(
            self.encoding or DEFAULT_ENCODING,
            errors=TEXT_DECODE_ERROR_MODE,
        )

    # ── Content-type predicates ───────────────────────────────────────────────

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
        """Response body size in bytes."""
        return len(self.body)

    # ── JSON parsing ──────────────────────────────────────────────────────────

    def json(self) -> Any:
        """Parse the response body as JSON.

        Attempts JSON decoding regardless of the ``Content-Type`` header —
        some servers return JSON with non-standard content types.

        Raises:
            ValueError: If the body is not valid JSON.
        """
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise ValueError("Malformed JSON response") from exc

    def json_safe(self) -> Any | None:
        """Parse the response body as JSON, returning ``None`` on failure."""
        try:
            return json.loads(self.text)
        except (json.JSONDecodeError, ValueError):
            return None

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (suitable for JSON/storage)."""
        return {
            "status_code": self.status_code,
            "reason": self.reason,
            "headers": dict(self.headers),
            "body": self.text,
            "elapsed": self.elapsed,
            "timestamp": self.timestamp.isoformat(),
            "content_type": self.content_type,
            "size": self.size,
            "sent_url": self.sent_url,
            "connection_info": self.connection_info,
        }
