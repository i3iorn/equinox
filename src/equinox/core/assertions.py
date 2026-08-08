"""Assertion evaluation helpers — used by both the GUI and CLI."""

import logging
from datetime import timedelta
from typing import Any, Protocol

from collections.abc import Callable

logger = logging.getLogger(__name__)


class _ResponseLike(Protocol):
    status_code: int
    headers: dict[str, str]
    elapsed: timedelta | float

    @property
    def text(self) -> str: ...

    def json(self) -> Any: ...


def _status_assert(expected: str, response: _ResponseLike) -> tuple[bool, str]:
    actual = str(response.status_code)
    passed = actual == expected
    return passed, f"status == {expected}  (got {actual})"


def _body_contains_assert(expected: str, response: _ResponseLike) -> tuple[bool, str]:
    body = response.text if hasattr(response, "text") else ""
    passed = expected in body
    return passed, f"body contains {expected!r}"


def _header_value_assert(field: str, expected: str, response: _ResponseLike) -> tuple[bool, str]:
    actual = response.headers.get(field.lower(), "")
    passed = actual == expected
    return passed, f"header '{field}' == {expected!r}  (got {actual!r})"


def _jsonpath_assert(field: str, expected: str, response: _ResponseLike) -> tuple[bool, str]:
    try:
        import jsonpath_ng.ext as _jpe

        expr = _jpe.parse(field)
        body_json = response.json()
        matches = [m.value for m in expr.find(body_json)]
        actual_str = str(matches[0]) if matches else ""
        passed = actual_str == expected
        return passed, f"jsonpath {field!r} == {expected!r}  (got {actual_str!r})"
    except ImportError:
        body = response.text if hasattr(response, "text") else ""
        passed = expected in body
        return passed, f"jsonpath {field!r} (fallback contains)  {expected!r}"


def _elapsed_lt_assert(expected: str, response: _ResponseLike) -> tuple[bool, str]:
    threshold = float(expected)
    elapsed_val = response.elapsed
    elapsed_ms = (
        elapsed_val.total_seconds() * 1000
        if isinstance(elapsed_val, timedelta)
        else float(elapsed_val) * 1000
    )
    passed = elapsed_ms < threshold
    return passed, f"elapsed < {threshold} ms  (got {elapsed_ms:.1f} ms)"


def evaluate_assertion(rule: dict[str, str], response: _ResponseLike) -> tuple[bool, str]:
    """Evaluate a single assertion rule against *response*."""
    a_type = rule.get("type", "status")
    field = rule.get("field", "")
    expected = rule.get("expected", "")

    handlers: dict[str, Callable[[], tuple[bool, str]]] = {
        "status": lambda: _status_assert(expected, response),
        "body_contains": lambda: _body_contains_assert(expected, response),
        "header_value": lambda: _header_value_assert(field, expected, response),
        "jsonpath": lambda: _jsonpath_assert(field, expected, response),
        "elapsed_lt": lambda: _elapsed_lt_assert(expected, response),
    }

    if a_type not in handlers:
        msg = f"unknown assertion type: {a_type!r}"
        logger.warning("Unknown assertion type %r — skipping", a_type)
        return False, msg

    try:
        passed, msg = handlers[a_type]()
        logger.debug("Assertion %s: %s — %s", a_type, "PASS" if passed else "FAIL", msg)
        return passed, msg
    except Exception as exc:
        msg = f"{a_type} — error: {exc}"
        logger.warning("Assertion %r raised an error: %s", a_type, exc, exc_info=True)
        return False, msg
