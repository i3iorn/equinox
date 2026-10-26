"""List/search fast paths must not materialize body BLOBs (issue #72).

``request_body``/``response_body`` hold up to MAX_BODY_SIZE each; the sidebar
list and search render method/URL/status only. These tests pin that the
projection queries never return body columns — bodies are fetched only by the
detail path (``get_history``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from equinox.core.request import Request, Response
from equinox.storage.history import HistoryManager

_LARGE_BODY = "x" * 10_000


@pytest.fixture
def mgr(tmp_path):
    from equinox.storage.database import Database

    return HistoryManager(Database(str(tmp_path / "history.db")))


def _response(status: int = 201) -> MagicMock:
    response = MagicMock(spec=Response)
    response.status_code = status
    response.reason = "Created"
    response.body = _LARGE_BODY
    response.headers = {"X-Tag": "t"}
    response.elapsed = 0.05
    return response


@pytest.fixture
def saved_id(mgr) -> int:
    request = Request(
        method="POST",
        url="https://api.example.com/upload",
        body=_LARGE_BODY,
        headers={"Content-Type": "text/plain"},
    )
    return mgr.save_history(request, _response())


@pytest.fixture
def saved_request_id(mgr, saved_id) -> int | None:
    """The stored request_id of the saved history entry (may be None when the
    Request carried no ID)."""
    row = mgr.get_history(saved_id)
    return row.get("request_id") if row else None


def test_list_history_excludes_body_columns(mgr, saved_id):
    rows = mgr.list_history()
    assert len(rows) == 1
    assert "request_body" not in rows[0]
    assert "response_body" not in rows[0]


def test_list_history_per_request_excludes_body_columns(mgr, saved_request_id):
    if saved_request_id is None:
        pytest.skip("Request carried no saved-request ID")
    rows = mgr.list_history(request_id=saved_request_id)
    assert rows
    assert "request_body" not in rows[0]
    assert "response_body" not in rows[0]


def test_search_history_excludes_body_columns(mgr, saved_id):
    rows = mgr.search_history(query="upload")
    assert rows
    assert "request_body" not in rows[0]
    assert "response_body" not in rows[0]


def test_search_history_body_regex_still_finds_results(mgr, saved_id):
    # Body regex search streams response_body through the post-filter (the
    # regex needs it), but the heavy request_body BLOB is still dropped.
    rows = mgr.search_history(body_regex=_LARGE_BODY[:20])
    assert rows
    assert "request_body" not in rows[0]


def test_get_history_detail_includes_bodies(mgr, saved_id):
    detail = mgr.get_history(saved_id)
    assert detail is not None
    assert detail["request_body"] == _LARGE_BODY
    assert detail["response_body"] == _LARGE_BODY
