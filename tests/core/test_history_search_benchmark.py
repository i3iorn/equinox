"""Regression checks for the history-search performance harness."""

from __future__ import annotations

import pytest

from equinox.core.perf import run_history_search_benchmark


@pytest.mark.parametrize(
    "entries,runs",
    [
        (100, 3),
        (250, 5),
    ],
)
def test_history_search_benchmark_returns_expected_metrics(entries: int, runs: int) -> None:
    metrics = run_history_search_benchmark(entries=entries, runs=runs)

    assert metrics["entries"] == entries
    assert metrics["runs"] == runs
    assert metrics["min_ms"] >= 0
    assert metrics["avg_ms"] >= metrics["min_ms"]
    assert metrics["median_ms"] >= metrics["min_ms"]
    assert metrics["max_ms"] >= metrics["p95_ms"]


def test_history_search_benchmark_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        run_history_search_benchmark(entries=0, runs=1)
    with pytest.raises(ValueError):
        run_history_search_benchmark(entries=1, runs=0)

