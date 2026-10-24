"""save_history hot-path overhead guards (issue #75).

Two behaviours are pinned:

* The prune COUNT(*) runs only every ``_PRUNE_CHECK_INTERVAL`` saves, not on
  every save — a full index scan per request save was pure overhead.
* The history insert and the history_index insert land in one transaction,
  halving WAL fsyncs and keeping the index consistent on failure.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from equinox.core.exceptions import StorageError
from equinox.core.request import Request, Response
from equinox.storage.database import Database
from equinox.storage.history import HistoryManager


def _response(status: int = 200) -> MagicMock:
    response = MagicMock(spec=Response)
    response.status_code = status
    response.reason = "OK"
    response.body = b"{}"
    response.headers = {"content-type": "application/json"}
    response.elapsed = 0.05
    return response


def _save(mgr: HistoryManager, url: str) -> int:
    return mgr.save_history(Request(method="GET", url=url), _response())


@pytest.fixture
def mgr(tmp_path):
    return HistoryManager(Database(str(tmp_path / "history.db")))


def test_prune_count_runs_only_every_interval(mgr):
    """The prune-check COUNT must not run on every save: it runs on the
    Nth save (counter hits the interval and resets), skipping N-1 saves."""
    for i in range(HistoryManager._PRUNE_CHECK_INTERVAL):
        _save(mgr, f"https://api.example.com/{i}")

    # Save #50 triggered the check and reset the counter.
    assert mgr._saves_since_prune_check == 0


def test_counter_skips_between_checks(mgr):
    for i in range(HistoryManager._PRUNE_CHECK_INTERVAL - 1):
        _save(mgr, f"https://api.example.com/{i}")

    assert mgr._saves_since_prune_check == HistoryManager._PRUNE_CHECK_INTERVAL - 1


def test_insert_and_index_share_one_transaction(mgr):
    """If the index write fails, the history row must roll back too."""

    def failing_index(*args, **kwargs):
        raise RuntimeError("simulated index failure")

    original = mgr._indexer.index
    mgr._indexer.index = failing_index  # type: ignore[method-assign]
    try:
        with pytest.raises(StorageError):
            _save(mgr, "https://api.example.com/failed")
    finally:
        mgr._indexer.index = original  # type: ignore[method-assign]

    assert mgr.list_history() == []


def test_successful_save_writes_history_and_index(mgr):
    _save(mgr, "https://api.example.com/ok")

    rows = mgr.search_history(query="ok")
    assert len(rows) == 1  # index row exists and is searchable
