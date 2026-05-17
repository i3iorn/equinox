"""Benchmark harness for history search/index performance.

Usage:
    python scripts/benchmark_history_search.py --entries 5000 --runs 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import List

from equinox.core.request import Request, Response
from equinox.storage import Database, HistoryManager


def _build_response(request: Request, status_code: int, elapsed: float) -> Response:
    return Response(
        status_code=status_code,
        reason="OK" if status_code < 400 else "ERROR",
        headers={"content-type": "application/json"},
        body=b'{"ok": true}',
        elapsed=elapsed,
        request=request,
    )


def _seed_history(manager: HistoryManager, entries: int) -> None:
    for i in range(entries):
        method = "GET" if i % 2 == 0 else "POST"
        req = Request(
            method=method,
            url=f"https://api.example.com/v1/items/{i % 200}?page={i % 20}",
            headers={"Accept": "application/json"},
            params={},
            body='{"name": "bench"}' if method == "POST" else None,
        )
        manager.save_history(
            req,
            response=_build_response(req, 200 if i % 10 else 500, elapsed=0.01 + (i % 7) * 0.002),
        )


def _measure_search(manager: HistoryManager, runs: int) -> List[float]:
    timings_ms: List[float] = []
    for i in range(runs):
        t0 = time.perf_counter()
        manager.search_history(
            query="/v1/items",
            method="GET",
            status_class="2xx",
            limit=100,
            offset=i % 20,
        )
        timings_ms.append((time.perf_counter() - t0) * 1000.0)
    return timings_ms


def _summarize(timings_ms: List[float], entries: int, runs: int) -> dict:
    ordered = sorted(timings_ms)
    p95_idx = max(0, int(len(ordered) * 0.95) - 1)
    return {
        "entries": entries,
        "runs": runs,
        "min_ms": round(min(ordered), 3),
        "avg_ms": round(statistics.mean(ordered), 3),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_idx], 3),
        "max_ms": round(max(ordered), 3),
    }


def run_benchmark(entries: int, runs: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="equinox-bench-") as tmp:
        db_path = Path(tmp) / "bench.db"
        db = Database(str(db_path))
        try:
            manager = HistoryManager(db)
            _seed_history(manager, entries)
            timings = _measure_search(manager, runs)
            return _summarize(timings, entries=entries, runs=runs)
        finally:
            db.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark Equinox history search performance")
    parser.add_argument("--entries", type=int, default=5000, help="Number of history rows to seed")
    parser.add_argument("--runs", type=int, default=20, help="Number of search benchmark runs")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.entries <= 0 or args.runs <= 0:
        raise SystemExit("--entries and --runs must be positive integers")

    result = run_benchmark(entries=args.entries, runs=args.runs)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
