"""Extended coverage tests for equinox.application.history.facade."""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import Mock

from equinox.application.history import HistoryFacade
from equinox.core.request import Request

# ── _coerce_to_dict ────────────────────────────────────────────────────────────


class TestCoerceToDict:
    def test_plain_dict_returned_as_copy(self) -> None:
        original = {"a": 1}
        result = HistoryFacade._coerce_to_dict(original, "headers")
        assert result == {"a": 1}
        assert result is not original

    def test_mapping_like_coerced_to_dict(self) -> None:
        class MyMapping(Mapping):
            def __init__(self):
                self._data = {"k": "v"}

            def __getitem__(self, key):
                return self._data[key]

            def __iter__(self):
                return iter(self._data)

            def __len__(self):
                return len(self._data)

        result = HistoryFacade._coerce_to_dict(MyMapping(), "headers")
        assert result == {"k": "v"}

    def test_broken_mapping_returns_empty_dict(self) -> None:
        class BrokenMapping(Mapping):
            def __getitem__(self, key):
                raise RuntimeError("broken")

            def __iter__(self):
                raise RuntimeError("broken")

            def __len__(self):
                return 0

            def items(self):
                raise RuntimeError("items broken")

        result = HistoryFacade._coerce_to_dict(BrokenMapping(), "headers")
        assert result == {}

    def test_non_mapping_returns_empty_dict(self) -> None:
        result = HistoryFacade._coerce_to_dict(42, "headers")
        assert result == {}

    def test_none_returns_empty_dict(self) -> None:
        result = HistoryFacade._coerce_to_dict(None, "headers")
        assert result == {}


# ── _coerce_body_to_bytes ──────────────────────────────────────────────────────


class TestCoerceBodyToBytes:
    def test_bytes_input_returned_unchanged(self) -> None:
        raw = b"hello world"
        result = HistoryFacade._coerce_body_to_bytes(raw)
        assert result is raw

    def test_str_input_encoded_to_utf8(self) -> None:
        result = HistoryFacade._coerce_body_to_bytes("hello")
        assert result == b"hello"

    def test_non_string_converted_via_str(self) -> None:
        result = HistoryFacade._coerce_body_to_bytes(12345)
        assert result == b"12345"

    def test_none_converts_to_bytes(self) -> None:
        result = HistoryFacade._coerce_body_to_bytes(None)
        assert result == b"None"

    def test_list_converts_via_str(self) -> None:
        result = HistoryFacade._coerce_body_to_bytes([1, 2])
        assert b"1" in result


# ── _parse_timestamp ───────────────────────────────────────────────────────────


class TestParseTimestamp:
    def test_none_returns_none(self) -> None:
        assert HistoryFacade._parse_timestamp(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert HistoryFacade._parse_timestamp("") is None

    def test_zero_returns_none(self) -> None:
        assert HistoryFacade._parse_timestamp(0) is None

    def test_valid_iso_string_parsed(self) -> None:
        result = HistoryFacade._parse_timestamp("2025-01-15T12:00:00")
        assert result is not None
        assert result.year == 2025

    def test_invalid_string_returns_none(self) -> None:
        result = HistoryFacade._parse_timestamp("not-a-date")
        assert result is None

    def test_invalid_type_returns_none(self) -> None:
        result = HistoryFacade._parse_timestamp(object())
        assert result is None


# ── request_from_entry ─────────────────────────────────────────────────────────


class TestRequestFromEntry:
    def test_bytes_body_decoded_to_str(self) -> None:
        entry = {
            "method": "POST",
            "url": "https://example.com",
            "request_headers": {},
            "request_params": {},
            "request_body": b'{"key": "value"}',
        }
        request = HistoryFacade.request_from_entry(entry)
        assert request.body == '{"key": "value"}'

    def test_non_string_body_converted(self) -> None:
        entry = {
            "method": "GET",
            "url": "https://example.com",
            "request_headers": {},
            "request_params": {},
            "request_body": 12345,
        }
        request = HistoryFacade.request_from_entry(entry)
        assert request.body == "12345"

    def test_none_body_stays_none(self) -> None:
        entry = {
            "method": "GET",
            "url": "https://example.com",
            "request_headers": {},
            "request_params": {},
            "request_body": None,
        }
        request = HistoryFacade.request_from_entry(entry)
        assert request.body is None

    def test_missing_method_defaults_to_get(self) -> None:
        entry = {"url": "https://example.com"}
        request = HistoryFacade.request_from_entry(entry)
        assert request.method == "GET"


# ── response_from_entry ────────────────────────────────────────────────────────


class TestResponseFromEntry:
    def test_missing_status_code_returns_none(self) -> None:
        entry = {"method": "GET", "url": "https://example.com"}
        request = HistoryFacade.request_from_entry(entry)
        result = HistoryFacade.response_from_entry(entry, request, history_id=5)
        assert result is None

    def test_none_status_code_returns_none(self) -> None:
        entry = {"status_code": None}
        request = Request(method="GET", url="https://example.com")
        result = HistoryFacade.response_from_entry(entry, request)
        assert result is None

    def test_valid_entry_builds_response(self) -> None:
        entry = {
            "status_code": 200,
            "reason": "OK",
            "response_headers": {"content-type": "application/json"},
            "response_body": '{"ok": true}',
            "elapsed": 0.05,
            "executed_at": "2025-06-01T10:00:00",
        }
        request = Request(method="GET", url="https://example.com")
        response = HistoryFacade.response_from_entry(entry, request, history_id=9)
        assert response is not None
        assert response.status_code == 200

    def test_response_construction_exception_returns_none(self) -> None:
        """When Response() raises, response_from_entry should return None."""
        entry = {
            "status_code": "not-an-int-and-will-blow-up",
            "response_body": b"body",
        }
        request = Request(method="GET", url="https://example.com")
        result = HistoryFacade.response_from_entry(entry, request, history_id=99)
        assert result is None

    def test_missing_executed_at_uses_now(self) -> None:
        entry = {
            "status_code": 204,
            "reason": "No Content",
            "response_body": "",
        }
        request = Request(method="DELETE", url="https://example.com/resource")
        response = HistoryFacade.response_from_entry(entry, request)
        assert response is not None
        assert response.status_code == 204


# ── Delegation wrappers ────────────────────────────────────────────────────────


class TestHistoryFacadeDelegation:
    def test_get_history_delegates(self) -> None:
        manager = Mock()
        manager.get_history.return_value = {"id": 7}
        facade = HistoryFacade(db=Mock(), history_manager=manager)
        assert facade.get_history(7) == {"id": 7}
        manager.get_history.assert_called_once_with(7)

    def test_clear_history_without_days(self) -> None:
        manager = Mock()
        facade = HistoryFacade(db=Mock(), history_manager=manager)
        facade.clear_history()
        manager.clear_history.assert_called_once_with(days=None)
