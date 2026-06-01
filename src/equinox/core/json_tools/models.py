from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Literal

EventType = Literal[
    "start_object",
    "end_object",
    "start_array",
    "end_array",
    "key",
    "value",
]


@dataclass(frozen=True)
class JsonErrorDetail:
    """Structured error information for safe JSON operations."""

    message: str
    context: str | None = None


@dataclass(frozen=True)
class JsonResult:
    """Structured result for JSON conversions."""

    value: Any
    elapsed_ms: int
