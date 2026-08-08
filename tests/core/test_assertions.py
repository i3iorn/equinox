"""Tests for core/assertions.py — assertion evaluation engine."""

from unittest.mock import Mock

import pytest

from equinox.core.assertions import evaluate_assertion


def _mock_response(
    status_code=200,
    headers=None,
    text="",
    body_json=None,
    elapsed=0.1,
):
    """Build a lightweight mock response for assertion tests."""
    resp = Mock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = text
    resp.elapsed = elapsed
    if body_json is not None:
        resp.json.return_value = body_json
    else:
        resp.json.side_effect = ValueError("No JSON body")
    return resp


# ── status assertions ─────────────────────────────────────────────────────


class TestStatusAssertion:
    def test_status_pass(self):
        resp = _mock_response(status_code=200)
        passed, msg = evaluate_assertion({"type": "status", "expected": "200"}, resp)
        assert passed is True
        assert "200" in msg

    def test_status_fail(self):
        resp = _mock_response(status_code=404)
        passed, msg = evaluate_assertion({"type": "status", "expected": "200"}, resp)
        assert passed is False
        assert "404" in msg

    def test_status_default_type(self):
        """When 'type' key is missing, defaults to 'status'."""
        resp = _mock_response(status_code=201)
        passed, _ = evaluate_assertion({"expected": "201"}, resp)
        assert passed is True


# ── body_contains assertions ──────────────────────────────────────────────


class TestBodyContainsAssertion:
    def test_body_contains_pass(self):
        resp = _mock_response(text='{"users": [{"id": 1}]}')
        passed, msg = evaluate_assertion({"type": "body_contains", "expected": "users"}, resp)
        assert passed is True
        assert "users" in msg

    def test_body_contains_fail(self):
        resp = _mock_response(text="hello world")
        passed, msg = evaluate_assertion({"type": "body_contains", "expected": "foobar"}, resp)
        assert passed is False

    def test_body_contains_no_text_attr(self):
        """Response without .text attribute uses empty string."""
        resp = Mock(spec=[])  # no .text attribute
        resp.status_code = 200
        passed, _ = evaluate_assertion({"type": "body_contains", "expected": "anything"}, resp)
        assert passed is False


# ── header_value assertions ───────────────────────────────────────────────


class TestHeaderValueAssertion:
    def test_header_value_pass(self):
        resp = _mock_response(headers={"content-type": "application/json"})
        passed, msg = evaluate_assertion(
            {"type": "header_value", "field": "Content-Type", "expected": "application/json"},
            resp,
        )
        assert passed is True

    def test_header_value_fail(self):
        resp = _mock_response(headers={"content-type": "text/html"})
        passed, msg = evaluate_assertion(
            {"type": "header_value", "field": "Content-Type", "expected": "application/json"},
            resp,
        )
        assert passed is False
        assert "text/html" in msg

    def test_header_missing(self):
        resp = _mock_response(headers={})
        passed, msg = evaluate_assertion(
            {"type": "header_value", "field": "X-Missing", "expected": "value"},
            resp,
        )
        assert passed is False


# ── jsonpath assertions ───────────────────────────────────────────────────


class TestJsonpathAssertion:
    def test_jsonpath_pass(self):
        """jsonpath match when jsonpath_ng is available."""
        try:
            import jsonpath_ng.ext  # noqa: F401
        except ImportError:
            pytest.skip("jsonpath_ng not installed")

        resp = _mock_response(body_json={"data": {"id": 42}})
        passed, msg = evaluate_assertion(
            {"type": "jsonpath", "field": "$.data.id", "expected": "42"},
            resp,
        )
        assert passed is True

    def test_jsonpath_fail(self):
        try:
            import jsonpath_ng.ext  # noqa: F401
        except ImportError:
            pytest.skip("jsonpath_ng not installed")

        resp = _mock_response(body_json={"data": {"id": 99}})
        passed, msg = evaluate_assertion(
            {"type": "jsonpath", "field": "$.data.id", "expected": "42"},
            resp,
        )
        assert passed is False
        assert "99" in msg

    def test_jsonpath_no_match(self):
        try:
            import jsonpath_ng.ext  # noqa: F401
        except ImportError:
            pytest.skip("jsonpath_ng not installed")

        resp = _mock_response(body_json={"other": "value"})
        passed, msg = evaluate_assertion(
            {"type": "jsonpath", "field": "$.missing.path", "expected": "x"},
            resp,
        )
        assert passed is False

    def test_jsonpath_fallback_without_library(self):
        """When jsonpath_ng is not installed, falls back to body contains."""
        # We can't easily remove jsonpath_ng at runtime, but we can test the
        # import-error branch by monkeypatching.
        import sys

        saved = sys.modules.get("jsonpath_ng.ext")
        sys.modules["jsonpath_ng.ext"] = None  # force ImportError on import

        try:
            resp = _mock_response(text='{"data": "hello"}')
            passed, msg = evaluate_assertion(
                {"type": "jsonpath", "field": "$.data", "expected": "hello"},
                resp,
            )
            assert passed is True
            assert "fallback" in msg.lower()
        finally:
            if saved is not None:
                sys.modules["jsonpath_ng.ext"] = saved
            else:
                sys.modules.pop("jsonpath_ng.ext", None)


# ── elapsed_lt assertions ─────────────────────────────────────────────────


class TestElapsedLtAssertion:
    def test_elapsed_pass(self):
        resp = _mock_response(elapsed=0.05)  # 50 ms
        passed, msg = evaluate_assertion(
            {"type": "elapsed_lt", "expected": "100"},
            resp,  # threshold 100 ms
        )
        assert passed is True
        assert "50.0 ms" in msg

    def test_elapsed_fail(self):
        resp = _mock_response(elapsed=0.5)  # 500 ms
        passed, msg = evaluate_assertion({"type": "elapsed_lt", "expected": "100"}, resp)
        assert passed is False
        assert "500.0 ms" in msg


# ── Unknown type ──────────────────────────────────────────────────────────


class TestUnknownAssertion:
    def test_unknown_type(self):
        resp = _mock_response()
        passed, msg = evaluate_assertion({"type": "nonexistent_type", "expected": "x"}, resp)
        assert passed is False
        assert "unknown assertion type" in msg


# ── Error handling ────────────────────────────────────────────────────────


class TestAssertionErrors:
    def test_invalid_elapsed_threshold(self):
        resp = _mock_response(elapsed=0.1)
        passed, msg = evaluate_assertion({"type": "elapsed_lt", "expected": "not-a-number"}, resp)
        assert passed is False
        assert "error" in msg.lower()

    def test_exception_in_assertion(self):
        """Response that throws on attribute access is caught."""
        resp = Mock()
        resp.status_code = property(lambda self: (_ for _ in ()).throw(RuntimeError("fail")))
        # Accessing status_code will raise TypeError at minimum
        passed, msg = evaluate_assertion({"type": "status", "expected": "200"}, resp)
        # Should not raise — returns (False, error message)
        assert passed is False
