from __future__ import annotations

from unittest.mock import Mock

from equinox.application.requests import RequestHistoryService
from equinox.core.request import Request


def test_request_history_service_lists_recent_unique_urls() -> None:
    manager = Mock()
    manager.list_history.return_value = [
        {"url": "https://api.example.com/a"},
        {"url": "https://api.example.com/b"},
        {"url": "https://api.example.com/a"},
        {"url": ""},
    ]
    service = RequestHistoryService(db=Mock(), history_manager=manager)

    result = service.list_recent_urls(limit=10)

    assert result == [
        "https://api.example.com/a",
        "https://api.example.com/b",
    ]
    manager.list_history.assert_called_once_with(limit=10)


def test_request_history_service_saves_history_safely() -> None:
    manager = Mock()
    service = RequestHistoryService(db=Mock(), history_manager=manager)
    request = Request(method="GET", url="https://example.com")

    service.save_history_safe(request, error="boom")

    manager.save_history.assert_called_once_with(request, error="boom")

