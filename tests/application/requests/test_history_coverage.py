"""Extended coverage tests for equinox.application.requests.history."""

from __future__ import annotations

from unittest.mock import Mock

from equinox.application.requests import RequestHistoryService
from equinox.core.request import Request, Response


def _make_response(request: Request) -> Response:
    return Response(
        status_code=200,
        reason="OK",
        headers={},
        body=b"",
        elapsed=0.1,
        request=request,
    )


class TestRequestHistoryServiceCoverage:
    def test_save_history_safe_with_none_request_is_silent(self) -> None:
        manager = Mock()
        service = RequestHistoryService(db=Mock(), history_manager=manager)

        # Must not raise and must not call save_history
        service.save_history_safe(None)  # type: ignore[arg-type]

        manager.save_history.assert_not_called()

    def test_save_history_safe_with_response_calls_save(self) -> None:
        manager = Mock()
        service = RequestHistoryService(db=Mock(), history_manager=manager)
        request = Request(method="GET", url="https://example.com")
        response = _make_response(request)

        service.save_history_safe(request, response=response)

        manager.save_history.assert_called_once_with(request, response)

    def test_save_history_safe_with_no_response_and_no_error_is_noop(self) -> None:
        """When both response and error are None, save_history must not be called."""
        manager = Mock()
        service = RequestHistoryService(db=Mock(), history_manager=manager)
        request = Request(method="GET", url="https://example.com")

        service.save_history_safe(request)

        manager.save_history.assert_not_called()

    def test_save_history_safe_suppresses_storage_exception(self) -> None:
        manager = Mock()
        manager.save_history.side_effect = RuntimeError("storage down")
        service = RequestHistoryService(db=Mock(), history_manager=manager)
        request = Request(method="GET", url="https://example.com")

        # Must not raise
        service.save_history_safe(request, error="boom")

    def test_save_history_safe_with_error_string(self) -> None:
        manager = Mock()
        service = RequestHistoryService(db=Mock(), history_manager=manager)
        request = Request(method="GET", url="https://example.com")

        service.save_history_safe(request, error="Timeout")

        manager.save_history.assert_called_once_with(request, error="Timeout")
