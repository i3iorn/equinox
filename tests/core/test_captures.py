"""Unit tests for CaptureEngine — response data extraction."""

import json
from unittest.mock import MagicMock

import pytest

from equinox.core.captures import Capture, CaptureEngine, CaptureResult

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_response(status=200, body=None, headers=None):
    """Return a minimal mock response."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {k.lower(): v for k, v in (headers or {}).items()}
    if body is None:
        body = {}
    resp.json.return_value = body
    resp.text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# _extract_json
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractJson:
    def test_simple_key(self):
        data = {"id": 42, "name": "alice"}
        assert CaptureEngine._extract_json("id", data) == "42"
        assert CaptureEngine._extract_json("name", data) == "alice"

    def test_nested_key(self):
        data = {"user": {"id": 7, "email": "a@b.com"}}
        assert CaptureEngine._extract_json("user.id", data) == "7"
        assert CaptureEngine._extract_json("user.email", data) == "a@b.com"

    def test_array_index(self):
        data = {"tokens": ["tok_a", "tok_b", "tok_c"]}
        assert CaptureEngine._extract_json("tokens[0]", data) == "tok_a"
        assert CaptureEngine._extract_json("tokens[2]", data) == "tok_c"

    def test_nested_array_index(self):
        data = {"data": {"items": [{"name": "x"}, {"name": "y"}]}}
        assert CaptureEngine._extract_json("data.items[1].name", data) == "y"

    def test_empty_path_returns_whole_object(self):
        data = {"a": 1}
        result = CaptureEngine._extract_json("", data)
        assert json.loads(result) == data

    def test_string_value_returned_as_is(self):
        data = {"token": "abc123"}
        assert CaptureEngine._extract_json("token", data) == "abc123"

    def test_missing_key_raises_keyerror(self):
        data = {"a": 1}
        with pytest.raises(KeyError, match="'b'"):
            CaptureEngine._extract_json("b", data)

    def test_index_out_of_range_raises_index_error(self):
        data = {"items": [1, 2]}
        with pytest.raises(IndexError):
            CaptureEngine._extract_json("items[5]", data)

    def test_intermediate_not_dict_raises_type_error(self):
        data = {"user": "not-a-dict"}
        with pytest.raises(TypeError):
            CaptureEngine._extract_json("user.id", data)

    def test_invalid_segment_raises_value_error(self):
        data = {}
        with pytest.raises(ValueError, match="Invalid JSON path segment"):
            CaptureEngine._extract_json("bad-key!", data)

    def test_non_string_value_json_serialised(self):
        data = {"count": 5}
        assert CaptureEngine._extract_json("count", data) == "5"

    def test_nested_object_json_serialised(self):
        data = {"meta": {"k": "v"}}
        result = CaptureEngine._extract_json("meta", data)
        assert json.loads(result) == {"k": "v"}


# ─────────────────────────────────────────────────────────────────────────────
# _extract_header
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractHeader:
    def test_exact_match(self):
        resp = _make_response(headers={"content-type": "application/json"})
        assert CaptureEngine._extract_header("content-type", resp) == "application/json"

    def test_case_insensitive(self):
        resp = _make_response(headers={"content-type": "application/json"})
        assert CaptureEngine._extract_header("Content-Type", resp) == "application/json"
        assert CaptureEngine._extract_header("CONTENT-TYPE", resp) == "application/json"

    def test_missing_header_raises_key_error(self):
        """_extract_header should raise KeyError for absent headers so that
        apply_all records success=False instead of silently returning ''.
        """
        resp = _make_response()
        with pytest.raises(KeyError, match="x-missing"):
            CaptureEngine._extract_header("x-missing", resp)

    def test_custom_header(self):
        resp = _make_response(headers={"x-request-id": "req-42"})
        assert CaptureEngine._extract_header("X-Request-ID", resp) == "req-42"


# ─────────────────────────────────────────────────────────────────────────────
# _extract_regex
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractRegex:
    def test_capture_group_returns_group1(self):
        resp = MagicMock()
        resp.text = "order_id=88 and other stuff"
        assert CaptureEngine._extract_regex(r"order_id=(\d+)", resp) == "88"

    def test_no_capture_group_returns_full_match(self):
        resp = MagicMock()
        resp.text = "status: active"
        assert CaptureEngine._extract_regex(r"status: \w+", resp) == "status: active"

    def test_no_match_raises_value_error(self):
        resp = MagicMock()
        resp.text = "nothing here"
        with pytest.raises(ValueError, match="did not match"):
            CaptureEngine._extract_regex(r"order_id=(\d+)", resp)

    def test_multiline_body(self):
        resp = MagicMock()
        resp.text = "line1\ntoken=abc123\nline3"
        assert CaptureEngine._extract_regex(r"token=(\w+)", resp) == "abc123"


# ─────────────────────────────────────────────────────────────────────────────
# apply_all
# ─────────────────────────────────────────────────────────────────────────────


class TestApplyAll:
    def _make_full_response(self):
        resp = _make_response(
            status=200,
            body={"user": {"id": 42}, "tokens": ["tok_abc"]},
            headers={"content-type": "application/json", "x-request-id": "req-9"},
        )
        resp.text = "user_id=42 token=tok_abc order_id=88"
        return resp

    def test_all_source_types(self):
        resp = self._make_full_response()
        captures = [
            Capture("uid", "json", "user.id"),
            Capture("tok", "json", "tokens[0]"),
            Capture("ct", "header", "content-type"),
            Capture("ord", "regex", r"order_id=(\d+)"),
            Capture("status", "status", ""),
        ]
        results = CaptureEngine.apply_all(captures, resp)
        assert len(results) == 5
        assert results[0].value == "42" and results[0].success
        assert results[1].value == "tok_abc" and results[1].success
        assert results[2].value == "application/json" and results[2].success
        assert results[3].value == "88" and results[3].success
        assert results[4].value == "200" and results[4].success

    def test_failure_uses_default(self):
        resp = self._make_full_response()
        captures = [Capture("miss", "json", "no.such.key", default="fallback")]
        results = CaptureEngine.apply_all(captures, resp)
        assert results[0].success is False
        assert results[0].value == "fallback"
        assert results[0].error  # error message present

    def test_failure_without_default_uses_empty_string(self):
        resp = self._make_full_response()
        captures = [Capture("miss", "json", "no.key")]
        results = CaptureEngine.apply_all(captures, resp)
        assert results[0].success is False
        assert results[0].value == ""

    def test_never_raises(self):
        resp = MagicMock()
        resp.status_code = 500
        resp.json.side_effect = ValueError("not JSON")
        resp.text = ""
        resp.headers = {}
        captures = [
            Capture("a", "json", "x"),
            Capture("b", "header", "x-missing"),
            Capture("c", "regex", r"no match"),
        ]
        # Must not raise
        results = CaptureEngine.apply_all(captures, resp)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, CaptureResult)

    def test_empty_captures_list(self):
        resp = self._make_full_response()
        results = CaptureEngine.apply_all([], resp)
        assert results == []

    def test_unknown_source_records_failure(self):
        resp = self._make_full_response()
        captures = [Capture("x", "xml_path", "//id")]
        results = CaptureEngine.apply_all(captures, resp)
        assert results[0].success is False
        assert "Unknown capture source" in results[0].error


# ─────────────────────────────────────────────────────────────────────────────
# from_dict_list / to_dict_list round-trip
# ─────────────────────────────────────────────────────────────────────────────


class TestSerialisation:
    def test_round_trip(self):
        captures = [
            Capture("uid", "json", "user.id", ""),
            Capture("token", "header", "x-token", "none"),
            Capture("status", "status", "", ""),
        ]
        raw = CaptureEngine.to_dict_list(captures)
        assert len(raw) == 3
        assert raw[0] == {"variable": "uid", "source": "json", "path": "user.id", "default": ""}
        restored = CaptureEngine.from_dict_list(raw)
        assert restored == captures

    def test_from_dict_list_skips_empty_variable(self):
        raw = [
            {"variable": "", "source": "json", "path": "x"},
            {"source": "json", "path": "y"},  # missing variable key
            {"variable": "ok", "source": "status"},
        ]
        result = CaptureEngine.from_dict_list(raw)
        assert len(result) == 1
        assert result[0].variable == "ok"

    def test_from_dict_list_skips_non_dicts(self):
        raw = ["string", 42, None, {"variable": "v", "source": "json", "path": "p"}]
        result = CaptureEngine.from_dict_list(raw)
        assert len(result) == 1

    def test_from_dict_list_defaults(self):
        raw = [{"variable": "v"}]
        result = CaptureEngine.from_dict_list(raw)
        assert result[0].source == "json"
        assert result[0].path == ""
        assert result[0].default == ""
