from __future__ import annotations

from unittest.mock import Mock

from equinox.application.history import HistoryFacade
from equinox.core.request import Request


def test_history_facade_delegates_search_delete_clear_and_stats() -> None:
    manager = Mock()
    facade = HistoryFacade(db=Mock(), history_manager=manager)

    manager.search_history.return_value = [{"id": 1}]
    manager.get_stats.return_value = {"total": 1, "successful": 1, "failed": 0}

    assert facade.search_history(query="x") == [{"id": 1}]
    assert facade.get_stats()["total"] == 1
    facade.delete_history(7)
    facade.clear_history(days=30)

    manager.search_history.assert_called_once_with(query="x")
    manager.delete_history.assert_called_once_with(7)
    manager.clear_history.assert_called_once_with(days=30)


def test_history_facade_reconstructs_request_and_response_from_entry() -> None:
    facade = HistoryFacade(db=Mock(), history_manager=Mock())
    entry = {
        "method": "POST",
        "url": "https://api.example.com/items",
        "request_headers": {"content-type": "application/json"},
        "request_params": {"q": "x"},
        "request_body": '{"name":"demo"}',
        "status_code": 201,
        "reason": "Created",
        "response_headers": {"content-type": "application/json"},
        "response_body": '{"ok":true}',
        "elapsed": 0.12,
        "executed_at": "2025-01-01T00:00:00",
    }

    request = facade.request_from_entry(entry)
    response = facade.response_from_entry(entry, request, history_id=12)

    assert isinstance(request, Request)
    assert request.method == "POST"
    assert request.url == "https://api.example.com/items"
    assert request.body == '{"name":"demo"}'
    assert response is not None
    assert response.status_code == 201
    assert response.request.url == "https://api.example.com/items"

