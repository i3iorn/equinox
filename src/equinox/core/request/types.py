"""Shared constants and TypedDict types for the request package."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

__all__ = [
    "CaptureRule",
    "AssertionRule",
    "MultipartField",
]

# ── Default field values ──────────────────────────────────────────────────────

DEFAULT_TIMEOUT: float = 30.0
DEFAULT_METHOD: str = "GET"

# ── Response body decoding ────────────────────────────────────────────────────

DEFAULT_ENCODING: str = "utf-8"
TEXT_DECODE_ERROR_MODE: str = "replace"

# ── Content-Type header parsing ───────────────────────────────────────────────

CONTENT_TYPE_HEADER: str = "content-type"
CHARSET_PARAMETER: str = "charset"


# ── TypedDicts ────────────────────────────────────────────────────────────────


class CaptureRule(TypedDict, total=False):
    """Rule for capturing values from response bodies into variables.

    Used in post-request scripts to extract data from responses and store in
    collection or environment variables for use in subsequent requests.

    Attributes:
        variable: Name of the target variable to store the captured value.
        source:   Source of the value — "response_body", "response_header", etc.
        path:     JSONPath or similar accessor (e.g. "data.user.id").
        default:  Fallback value if the path does not match.
    """

    variable: str
    source: str
    path: str
    default: str


class AssertionRule(TypedDict, total=False):
    """Rule for asserting response properties for test automation.

    Attributes:
        type:     Assertion type — "status_code", "header", "body_contains", etc.
        field:    Optional field selector (e.g. header name, JSON path).
        expected: Expected value or pattern to match against.
    """

    type: str
    field: str
    expected: Any


class MultipartField(TypedDict):
    """Represents a single field in a multipart/form-data request body.

    Attributes:
        key:   Form field name.
        type:  "text" for plain text, "file" for file uploads.
        value: Field value (text content or file path for "file" type).
    """

    key: str
    type: Literal["text", "file"]
    value: str
