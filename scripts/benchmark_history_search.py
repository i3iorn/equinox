"""Benchmark harness for history search/index performance.

Usage:
    python scripts/benchmark_history_search.py --entries 5000 --runs 20
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

repo_src = Path(__file__).resolve().parents[1] / "src"
if str(repo_src) not in sys.path:
    sys.path.insert(0, str(repo_src))

from equinox.core.perf import run_history_search_benchmark


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark Equinox history search performance")
    parser.add_argument("--entries", type=int, default=5000, help="Number of history rows to seed")
    parser.add_argument("--runs", type=int, default=20, help="Number of search benchmark runs")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = run_history_search_benchmark(entries=args.entries, runs=args.runs)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
