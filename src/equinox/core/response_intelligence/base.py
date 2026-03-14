"""Abstract base class for response analyzers."""

import abc
from typing import List

from equinox.core.response_intelligence.models import (
    Category,
    Finding,
    AnalysisContext,
)


class Analyzer(abc.ABC):
    """Base class for all response intelligence analyzers.

    Subclasses must set ``analyzer_id`` and ``category`` and implement
    :meth:`analyze`.
    """

    analyzer_id: str = ""
    category: Category = Category.HINTS
    display_name: str = ""

    @abc.abstractmethod
    def analyze(self, ctx: AnalysisContext) -> List[Finding]:
        """Run the analysis and return zero or more findings."""

