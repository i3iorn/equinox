"""SQL filter construction and Python post-filters for history search."""
from __future__ import annotations

import logging
import re
from datetime import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple

from equinox.core.exceptions import SecurityError, ValidationError
from equinox.storage.database import Database
from equinox.storage.utils import coerce_body_to_str, safe_json_loads
from ._constants import _LIKE_ESCAPE_CLAUSE, _STATUS_CODE_RANGES
from ._serializer import _HistorySerializer

__all__ = ["_HistorySearcher"]

logger = logging.getLogger(__name__)


class _HistorySearcher:
    """Build SQL WHERE clauses and apply Python post-filters for history queries.

    Owns all search-related concerns so ``HistoryManager`` stays free of
    query-construction and post-filter mechanics.
    """

    MAX_REGEX_LENGTH = 500
    MAX_LIMIT        = 10_000

    def __init__(self, db: Database, serializer: _HistorySerializer) -> None:
        self._db         = db
        self._serializer = serializer

    # ── Public interface ──────────────────────────────────────────────────────

    def validate_pagination(self, limit: int, offset: int) -> None:
        """Raise if *limit* or *offset* are out of range."""
        if not isinstance(limit, int) or limit <= 0:
            raise ValidationError("Limit must be a positive integer")
        if limit > self.MAX_LIMIT:
            raise SecurityError(f"Limit too large (max {self.MAX_LIMIT})")
        if not isinstance(offset, int) or offset < 0:
            raise ValidationError("Offset must be a non-negative integer")

    def search(
        self,
        *,
        query: str = "",
        method: str = "",
        status_class: str = "",
        status_code: Optional[int] = None,
        body_regex: str = "",
        jsonpath: str = "",
        jsonpath_value: Optional[str] = None,
        content_type: str = "",
        header: str = "",
        min_elapsed: Optional[float] = None,
        max_elapsed: Optional[float] = None,
        executed_after: Optional[str] = None,
        executed_before: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Run a filtered history search and return decoded rows.

        SQL-level filters run first (fast, indexed).  Python post-filters
        (*body_regex*, *jsonpath*, *header*) are applied via a streaming
        cursor-based loop that doubles the batch size on each exhausted round-trip.
        """
        self.validate_pagination(limit, offset)

        compiled_regex  = self._compile_body_regex(body_regex)
        parsed_jsonpath = self._parse_jsonpath(jsonpath)

        conditions, params_list = self._build_sql_filters(
            query=query, method=method, status_code=status_code,
            status_class=status_class, content_type=content_type,
            min_elapsed=min_elapsed, max_elapsed=max_elapsed,
            executed_after=executed_after, executed_before=executed_before,
        )
        where_clause      = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        needs_post_filter = bool(compiled_regex or parsed_jsonpath or header)

        if not needs_post_filter:
            return self._fast_query(where_clause, params_list, limit, offset)

        return self._streaming_query(
            where_clause=where_clause, params_list=params_list,
            limit=limit, offset=offset,
            compiled_regex=compiled_regex,
            parsed_jsonpath=parsed_jsonpath, jsonpath_value=jsonpath_value,
            header=header,
        )

    # ── SQL fast-path ─────────────────────────────────────────────────────────

    def _fast_query(
        self,
        where_clause: str,
        params_list: List[Any],
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        sql  = (
            f"SELECT * FROM history {where_clause} "
            "ORDER BY executed_at DESC LIMIT ? OFFSET ?"
        )
        rows = self._db.fetchall(sql, tuple(params_list) + (limit, offset))
        return [self._serializer.decode_row(dict(row), row_id=row["id"]) for row in rows]

    # ── Streaming post-filter path ────────────────────────────────────────────

    def _streaming_query(
        self,
        *,
        where_clause: str,
        params_list: List[Any],
        limit: int,
        offset: int,
        compiled_regex: Optional["re.Pattern[str]"],
        parsed_jsonpath: Any,
        jsonpath_value: Optional[str],
        header: str,
    ) -> List[Dict[str, Any]]:
        """Fetch rows in growing batches until *limit* post-filter matches are collected."""
        header_name, header_val = self._parse_header_filter(header)
        result:        List[Dict[str, Any]] = []
        cursor_offset  = offset
        batch_size     = max(limit * 4, 200)
        sql_template   = (
            f"SELECT * FROM history {where_clause} "
            "ORDER BY executed_at DESC LIMIT ? OFFSET ?"
        )

        while len(result) < limit:
            batch = self._db.fetchall(
                sql_template, tuple(params_list) + (batch_size, cursor_offset)
            )
            if not batch:
                break

            for row in batch:
                if len(result) >= limit:
                    break
                decoded = self._serializer.decode_row(dict(row), row_id=row["id"])
                if compiled_regex and not self._matches_body_regex(decoded, compiled_regex):
                    continue
                if parsed_jsonpath and not self._matches_jsonpath(
                    decoded, parsed_jsonpath, jsonpath_value
                ):
                    continue
                if header_name and not self._matches_header(decoded, header_name, header_val):
                    continue
                result.append(decoded)

            if len(batch) < batch_size:
                break  # SQL cursor exhausted

            cursor_offset += batch_size
            batch_size     = min(batch_size * 2, self.MAX_LIMIT)

        return result

    # ── SQL filter construction ───────────────────────────────────────────────

    def _build_sql_filters(
        self,
        *,
        query: str,
        method: str,
        status_code: Optional[int],
        status_class: str,
        content_type: str,
        min_elapsed: Optional[float],
        max_elapsed: Optional[float],
        executed_after: Optional[str],
        executed_before: Optional[str],
    ) -> Tuple[List[str], List[Any]]:
        """Return ``(conditions, params)`` for the SQL WHERE clause."""
        conditions: List[str] = []
        params:     List[Any] = []

        if query and isinstance(query, str):
            like = f"%{self._escape_like(query)}%"
            conditions.append(
                f"(url LIKE ? {_LIKE_ESCAPE_CLAUSE}"
                f" OR request_body LIKE ? {_LIKE_ESCAPE_CLAUSE})"
            )
            params.extend([like, like])

        if method and isinstance(method, str):
            conditions.append("method = ?")
            params.append(method.upper())

        if status_code is not None:
            if not isinstance(status_code, int):
                raise ValidationError("status_code must be an integer")
            conditions.append("status_code = ?")
            params.append(status_code)
        else:
            status_class_lower = (status_class or "").lower()
            if status_class_lower in _STATUS_CODE_RANGES:
                start, end = _STATUS_CODE_RANGES[status_class_lower]
                conditions.append(f"status_code BETWEEN {start} AND {end}")
            elif status_class_lower == "errors":
                conditions.append("(status_code IS NULL OR status_code >= 400)")

        if content_type and isinstance(content_type, str):
            conditions.append(f"response_headers LIKE ? {_LIKE_ESCAPE_CLAUSE}")
            params.append(f"%{self._escape_like(content_type)}%")

        if min_elapsed is not None:
            conditions.append("elapsed >= ?")
            params.append(float(min_elapsed))
        if max_elapsed is not None:
            conditions.append("elapsed <= ?")
            params.append(float(max_elapsed))

        if executed_after and isinstance(executed_after, str):
            self._validate_iso_timestamp(executed_after, "executed_after")
            conditions.append("executed_at >= ?")
            params.append(executed_after)
        if executed_before and isinstance(executed_before, str):
            self._validate_iso_timestamp(executed_before, "executed_before")
            conditions.append("executed_at <= ?")
            params.append(executed_before)

        return conditions, params

    # ── Post-filter predicates ────────────────────────────────────────────────

    @staticmethod
    def _matches_body_regex(row: Dict[str, Any], compiled_regex: "re.Pattern[str]") -> bool:
        body = coerce_body_to_str(row.get("response_body") or "") or ""
        return bool(compiled_regex.search(body))

    @staticmethod
    def _matches_jsonpath(
        row: Dict[str, Any],
        parsed_jsonpath: Any,
        jsonpath_value: Optional[str] = None,
    ) -> bool:
        body = coerce_body_to_str(row.get("response_body") or "") or ""
        try:
            from equinox.storage.utils import safe_json_loads as _loads
            data = _loads(body)
            # safe_json_loads returns {} on failure; treat as no-match for
            # non-empty bodies that failed to parse.
            if body and body.strip() and data == {}:
                return False
        except Exception:
            return False
        matches = parsed_jsonpath.find(data)
        if not matches:
            return False
        return jsonpath_value is None or str(matches[0].value) == jsonpath_value

    @staticmethod
    def _matches_header(
        row: Dict[str, Any], header_name: str, header_val: str = ""
    ) -> bool:
        resp_headers = row.get("response_headers") or {}
        if isinstance(resp_headers, str):
            resp_headers = safe_json_loads(resp_headers, row_id=row.get("id"))
            if not isinstance(resp_headers, dict):
                resp_headers = {}
        for key, value in resp_headers.items():
            if header_name in key.lower():
                if not header_val or header_val in str(value).lower():
                    return True
        return False

    # ── Scalar helpers ────────────────────────────────────────────────────────

    def _compile_body_regex(self, body_regex: str) -> Optional["re.Pattern[str]"]:
        if not body_regex:
            return None
        if len(body_regex) > self.MAX_REGEX_LENGTH:
            raise ValidationError(
                f"Regex pattern too long (max {self.MAX_REGEX_LENGTH} chars)"
            )
        try:
            return re.compile(body_regex, re.IGNORECASE)
        except re.error as exc:
            raise ValidationError(f"Invalid regex pattern: {exc}")

    @staticmethod
    def _parse_jsonpath(jsonpath: str) -> Optional[Any]:
        if not jsonpath:
            return None
        try:
            from jsonpath_ng import parse as _jp_parse
            return _jp_parse(jsonpath)
        except Exception as exc:
            raise ValidationError(f"Invalid JSONPath expression: {exc}")

    @staticmethod
    def _validate_iso_timestamp(timestamp: str, label: str) -> None:
        if not isinstance(timestamp, str):
            raise ValidationError(f"{label} must be a string")
        # Strip trailing Z — Python < 3.11 ``fromisoformat`` doesn't handle it.
        try:
            _dt.fromisoformat(timestamp.rstrip("Z"))
        except ValueError:
            raise ValidationError(
                f"{label} must be in ISO-8601 format (e.g. 2026-03-23T16:20:00Z)"
            )

    @staticmethod
    def _escape_like(text: str) -> str:
        r"""Escape SQL LIKE metacharacters so they match literally with ``ESCAPE '\'``."""
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _parse_header_filter(header: str) -> Tuple[str, str]:
        """Parse ``'Name: value'`` into ``(name_lower, value_lower)``."""
        if not header:
            return "", ""
        if ":" in header:
            name, val = header.split(":", 1)
            return name.strip().lower(), val.strip().lower()
        return header.strip().lower(), ""

