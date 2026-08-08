"""
Pure search engine logic.
No Qt imports. No UI. Fully testable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import auto
from enum import Enum
from typing import Any

from .constants import ERROR_JSONPATH_IMPORT
from .constants import ERROR_NO_JSON
from .constants import MAX_MATCHES
from .constants import PREVIEW_MAX_VALUES
from .constants import PREVIEW_VALUE_LIMIT

logger = logging.getLogger(__name__)


class SearchMode(Enum):
    TEXT = auto()
    REGEX = auto()
    JSONPATH = auto()


@dataclass(frozen=True)
class SearchJobConfig:
    job_id: int
    mode: SearchMode
    term: str
    case_sensitive: bool
    doc_text: str
    json_obj: Any


@dataclass(frozen=True)
class SearchResult:
    offsets: list[tuple[int, int]]
    values: list[Any]
    preview: str


class SearchEngine:
    """Pure search engine with no UI dependencies."""

    def run(self, cfg: SearchJobConfig) -> SearchResult:
        if not cfg.term:
            return SearchResult([], [], "")

        try:
            if cfg.mode is SearchMode.JSONPATH:
                return self._search_jsonpath(cfg)
            if cfg.mode is SearchMode.REGEX:
                return self._search_regex(cfg)
            return self._search_text(cfg)
        except Exception:
            logger.exception("Unhandled error in search engine")
            return SearchResult([], [], "")

    # ────────────────────────────────────────────────────────────────
    # TEXT SEARCH
    # ────────────────────────────────────────────────────────────────

    def _search_text(self, cfg: SearchJobConfig) -> SearchResult:
        text = cfg.doc_text
        term = cfg.term

        if not cfg.case_sensitive:
            text = text.lower()
            term = term.lower()

        offsets: list[tuple[int, int]] = []
        start = 0

        while len(offsets) < MAX_MATCHES:
            idx = text.find(term, start)
            if idx == -1:
                break
            end = idx + len(cfg.term)
            offsets.append((idx, end))
            start = end

        return SearchResult(offsets, [], "")

    # ────────────────────────────────────────────────────────────────
    # REGEX SEARCH
    # ────────────────────────────────────────────────────────────────

    def _search_regex(self, cfg: SearchJobConfig) -> SearchResult:
        try:
            import re

            flags = 0 if cfg.case_sensitive else re.IGNORECASE
            pattern = re.compile(cfg.term, flags)
        except Exception:
            return SearchResult([], [], "invalid regex")

        offsets: list[tuple[int, int]] = []
        for match in pattern.finditer(cfg.doc_text):
            if len(offsets) >= MAX_MATCHES:
                break
            offsets.append((match.start(), match.end()))

        return SearchResult(offsets, [], "")

    # ────────────────────────────────────────────────────────────────
    # JSONPATH SEARCH
    # ────────────────────────────────────────────────────────────────

    def _search_jsonpath(self, cfg: SearchJobConfig) -> SearchResult:
        if cfg.json_obj is None:
            return SearchResult([], [], ERROR_NO_JSON)

        try:
            from jsonpath_ng.ext import parse as jp_parse
        except ImportError:
            return SearchResult([], [], ERROR_JSONPATH_IMPORT)

        try:
            expr = jp_parse(cfg.term)
            matches = expr.find(cfg.json_obj)
        except Exception as exc:
            return SearchResult([], [], f"⚠ {exc}")

        values = [m.value for m in matches]
        preview = self._build_preview(values)
        return SearchResult([], values, preview)

    # ────────────────────────────────────────────────────────────────
    # PREVIEW BUILDER
    # ────────────────────────────────────────────────────────────────

    def _build_preview(self, values: list[Any]) -> str:
        if not values:
            return "(path matched no values)"

        previews = []
        for v in values[:PREVIEW_MAX_VALUES]:
            s = json.dumps(v, ensure_ascii=False)
            previews.append(s if len(s) <= PREVIEW_VALUE_LIMIT else s[:47] + "…")

        preview = "  ·  ".join(previews)
        if len(values) > PREVIEW_MAX_VALUES:
            preview += f"  … (+{len(values) - PREVIEW_MAX_VALUES} more)"

        return f"→ {preview}"
