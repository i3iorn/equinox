"""HTTP header and cache-control helper utilities."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


def first_present_header(headers: Mapping[str, str], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = headers.get(key)
        if value is not None:
            return value
    return None


def parse_cache_control(cache_control: str) -> list:
    return [
        directive.strip() for directive in (cache_control or "").split(",") if directive.strip()
    ]


def summarize_cache_control(cache_control: str) -> str:
    low = (cache_control or "").lower()
    if "no-store" in low:
        return "no-store"
    if "no-cache" in low:
        return "revalidate"
    if "max-age=" in low:
        match = re.search(r"max-age=(\d+)", low)
        if match:
            secs = int(match.group(1))
            if secs >= 86400:
                return f"cached {secs // 86400}d"
            if secs >= 3600:
                return f"cached {secs // 3600}h"
            return f"cached {secs}s"
    return "present"
