"""Numeric sampling and percentile helpers."""

from __future__ import annotations

import math
from typing import List, Sequence


def coerce_numeric_samples(values: object, max_samples: int = 500) -> List[float]:
    if not isinstance(values, list):
        return []

    numeric: List[float] = []
    for item in values:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            numeric.append(value)

    if len(numeric) > max_samples:
        return numeric[-max_samples:]
    return numeric


def percentile(sorted_data: Sequence[float], pct: int) -> float:
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * pct / 100
    floor_idx = int(k)
    ceil_idx = floor_idx + 1
    if ceil_idx >= len(sorted_data):
        return float(sorted_data[floor_idx])
    return sorted_data[floor_idx] + (k - floor_idx) * (sorted_data[ceil_idx] - sorted_data[floor_idx])

