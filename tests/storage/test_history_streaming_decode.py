"""Streaming search post-filters must not decode rejected rows (issue #73).

The streaming path used to JSON-parse both header columns for every scanned
row *before* running the body-regex/JSONPath filters — with 100k rows the
header parsing dominated the scan cost even though the filters only need the
raw response_body text. These tests pin that:

* results keep the decoded-dict header shape (identical to the fast path),
* header JSON parsing happens only for rows that survive the filters.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from equinox.core.request import Request, Response
from equinox.storage.database import Database
from equinox.storage.history import HistoryManager


def _response(status: int = 200, body: str = "needle-in-body") -> MagicMock:
    response = MagicMock(spec=Response)
    response.status_code = status
    response.reason = "OK"
    response.body = body
    response.headers = {"Content-Type": "application/json"}
    response.elapsed = 0.05
    return response


def _save(mgr: HistoryManager, body: str, marker: str) -> int:
    request = Request(method="POST", url=f"https://api.example.com/{marker}")
    return mgr.save_history(request, _response(body=body))


@pytest.fixture
def mgr(tmp_path):
    return HistoryManager(Database(str(tmp_path / "history.db")))


def test_regex_results_keep_decoded_header_dicts(mgr):
    _save(mgr, "needle-in-body", "hit")
    _save(mgr, "nothing relevant", "miss")

    rows = mgr.search_history(body_regex="needle")

    assert len(rows) == 1
    assert rows[0]["url"].endswith("/hit")
    assert isinstance(rows[0]["request_headers"], dict)
    assert isinstance(rows[0]["response_headers"], dict)


def test_header_json_not_parsed_for_rejected_rows(mgr):
    """Rejected rows must skip the header JSON parse entirely."""
    _save(mgr, "needle-in-body", "hit")
    for i in range(10):
        _save(mgr, f"filler {i}", "miss")

    import equinox.storage.history._serializer as serializer_mod

    calls = {"n": 0}
    real_loads = serializer_mod.safe_json_loads

    def counting_loads(*args, **kwargs):
        calls["n"] += 1
        return real_loads(*args, **kwargs)

    with patch.object(serializer_mod, "safe_json_loads", counting_loads):
        rows = mgr.search_history(body_regex="needle", limit=10)

    assert len(rows) == 1
    # 11 rows scanned; only the match gets its two header columns parsed.
    assert calls["n"] == 2
