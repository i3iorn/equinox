"""Data models for the Response Intelligence engine."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from equinox.core.request import Request, Response


class Severity(Enum):
    """Finding severity level."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Category(Enum):
    """Analysis category."""

    SECURITY = "Security & Compliance"
    PERFORMANCE = "Performance & Efficiency"
    CONSISTENCY = "Consistency & Correctness"
    SERVER = "Server Intelligence"
    HINTS = "Developer Hints"


@dataclass
class Finding:
    """A single analysis finding."""

    category: Category
    severity: Severity
    title: str
    description: str
    analyzer_id: str
    recommendation: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisContext:
    """Context passed to every analyzer.

    Carries the request/response pair plus optional historical data
    needed by drift / percentile / anomaly analyzers.
    """

    request: Request
    response: Response
    history_rows: list[dict[str, Any]] = field(default_factory=list)
    endpoint_stats: dict[str, Any] | None = None
    stored_schema: dict[str, Any] | None = None
