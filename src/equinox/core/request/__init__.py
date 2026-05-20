"""Request and Response models for HTTP communication.

This package provides the core HTTP request/response abstractions used
throughout Equinox.  Sub-modules:

- :mod:`types`    — :class:`CaptureRule`, :class:`AssertionRule`,
                    :class:`MultipartField` TypedDicts and shared constants.
- :mod:`headers`  — :class:`HeaderDict`, an RFC 7230-compliant,
                    case-insensitive header container.
- :mod:`request`  — :class:`Request` dataclass with serialisation,
                    cURL export, and path/query-param URL building.
- :mod:`response` — :class:`Response` dataclass with content-type
                    detection, cached decoding, and JSON parsing.

All public symbols are re-exported here so existing callers that do::

    from equinox.core.request import Request, Response, HeaderDict

continue to work without any changes.
"""

from __future__ import annotations

from equinox.core.request.headers import HeaderDict
from equinox.core.request.request import Request
from equinox.core.request.response import Response
from equinox.core.request.types import AssertionRule, CaptureRule, MultipartField

__all__ = [
    "Request",
    "Response",
    "HeaderDict",
    "CaptureRule",
    "AssertionRule",
    "MultipartField",
]
