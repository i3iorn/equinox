"""Request history management package.

Public API
----------
- ``HistoryManager`` — CRUD and search over the ``history`` table.

Internal structure
------------------
  _constants  — ``_LIKE_ESCAPE_CLAUSE``, ``_STATUS_CODE_RANGES``
  _serializer — ``_HistorySerializer`` — Request/Response → storable primitives
  _indexer    — ``_HistoryIndexer``    — best-effort ``history_index`` maintenance
  _searcher   — ``_HistorySearcher``   — SQL filter building + Python post-filters
  manager     — ``HistoryManager``     — thin public orchestrator
"""
from __future__ import annotations

from .manager import HistoryManager

__all__ = ["HistoryManager"]
