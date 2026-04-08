"""Assertion evaluation helpers — used by both the GUI and CLI."""

from datetime import timedelta
from typing import Tuple


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
            return passed, f"status == {expected}  (got {actual})"

        elif a_type == "body_contains":
            body = response.text if hasattr(response, "text") else ""
            passed = expected in body
            return passed, f"body contains {expected!r}"

        elif a_type == "header_value":
            actual = response.headers.get(field.lower(), "")
            passed = actual == expected
            return passed, f"header '{field}' == {expected!r}  (got {actual!r})"

        elif a_type == "jsonpath":
            try:
                import jsonpath_ng.ext as _jpe  # type: ignore
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

        elif a_type == "elapsed_lt":
            threshold = float(expected)
            elapsed_val = response.elapsed
            if isinstance(elapsed_val, timedelta):
                elapsed_ms = elapsed_val.total_seconds() * 1000
            else:
                elapsed_ms = float(elapsed_val) * 1000
            passed = elapsed_ms < threshold
            return passed, f"elapsed < {threshold} ms  (got {elapsed_ms:.1f} ms)"

        else:
            return False, f"unknown assertion type: {a_type!r}"

    except Exception as exc:
        return False, f"{a_type} — error: {exc}"
