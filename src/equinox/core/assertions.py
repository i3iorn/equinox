"""Assertion evaluation helpers — used by both the GUI and CLI."""

import logging
from datetime import timedelta
from typing import Tuple

logger = logging.getLogger(__name__)


def evaluate_assertion(rule: dict, response) -> Tuple[bool, str]:
    """Evaluate a single assertion rule against *response*.

    *response* must expose:
    - ``status_code`` (int)
    - ``headers`` (dict, keys lower-cased)
    - ``elapsed`` (float, seconds)
    - ``text`` (str)
    - ``json()`` method (optional)

    Returns:
        ``(passed: bool, message: str)``
    """
    a_type   = rule.get("type", "status")
    field    = rule.get("field", "")
    expected = rule.get("expected", "")

    try:
        if a_type == "status":
            actual = str(response.status_code)
            passed = actual == expected
            msg = f"status == {expected}  (got {actual})"
            logger.debug("Assertion %s: %s — %s", a_type, "PASS" if passed else "FAIL", msg)
            return passed, msg

        elif a_type == "body_contains":
            body = response.text if hasattr(response, "text") else ""
            passed = expected in body
            msg = f"body contains {expected!r}"
            logger.debug("Assertion %s: %s — %s", a_type, "PASS" if passed else "FAIL", msg)
            return passed, msg

        elif a_type == "header_value":
            actual = response.headers.get(field.lower(), "")
            passed = actual == expected
            msg = f"header '{field}' == {expected!r}  (got {actual!r})"
            logger.debug("Assertion %s: %s — field=%r", a_type, "PASS" if passed else "FAIL", field)
            return passed, msg

        elif a_type == "jsonpath":
            try:
                import jsonpath_ng.ext as _jpe  # type: ignore
                expr = _jpe.parse(field)
                body_json = response.json()
                matches = [m.value for m in expr.find(body_json)]
                actual_str = str(matches[0]) if matches else ""
                passed = actual_str == expected
                msg = f"jsonpath {field!r} == {expected!r}  (got {actual_str!r})"
                logger.debug("Assertion %s: %s — path=%r", a_type, "PASS" if passed else "FAIL", field)
                return passed, msg
            except ImportError:
                body = response.text if hasattr(response, "text") else ""
                passed = expected in body
                msg = f"jsonpath {field!r} (fallback contains)  {expected!r}"
                logger.debug("Assertion %s (jsonpath_ng unavailable, fallback): %s", a_type, "PASS" if passed else "FAIL")
                return passed, msg

        elif a_type == "elapsed_lt":
            threshold = float(expected)
            elapsed_val = response.elapsed
            if isinstance(elapsed_val, timedelta):
                elapsed_ms = elapsed_val.total_seconds() * 1000
            else:
                elapsed_ms = float(elapsed_val) * 1000
            passed = elapsed_ms < threshold
            msg = f"elapsed < {threshold} ms  (got {elapsed_ms:.1f} ms)"
            logger.debug("Assertion %s: %s — elapsed=%.1f ms, threshold=%.1f ms",
                         a_type, "PASS" if passed else "FAIL", elapsed_ms, threshold)
            return passed, msg

        else:
            msg = f"unknown assertion type: {a_type!r}"
            logger.warning("Unknown assertion type %r — skipping", a_type)
            return False, msg

    except Exception as exc:
        msg = f"{a_type} — error: {exc}"
        logger.warning("Assertion %r raised an error: %s", a_type, exc, exc_info=True)
        return False, msg
