"""Request history management"""

import logging
import hashlib
import re
from typing import List, Dict, Any, Optional, Tuple
from collections import namedtuple

from equinox.core.redact import redact_headers, redact_url
from equinox.storage.database import Database
from equinox.core.request import Request, Response
from equinox.core.exceptions import StorageError, ValidationError, SecurityError
from equinox.storage.utils import (
    require_positive_int as _require_positive_int_impl,
    coerce_body_to_str,
    safe_json_dumps,
    safe_json_loads,
)
from equinox.core import urls

logger = logging.getLogger(__name__)

# Indexing guards for history_index payload sizes
MAX_INDEX_PATH_SEGMENTS = 64
MAX_INDEX_QUERY_PARAMS = 128

# Response fields as a named tuple for clarity
ResponseFields = namedtuple(
    "ResponseFields",
    ["status_code", "reason", "elapsed", "headers_json", "body"],
)


class HistoryManager:
    """Manage request/response history"""

    MAX_HISTORY_ENTRIES = 100000
    MAX_BODY_SIZE = 10 * 1024 * 1024   # 10 MB
    MAX_HEADERS_SIZE = 100 * 1024       # 100 KB
    MAX_URL_LENGTH = 2048
    MAX_ERROR_MESSAGE_LENGTH = 10000
    MAX_REGEX_LENGTH = 500
    DEFAULT_LIMIT = 100
    MAX_LIMIT = 10000
    
    # SQL LIKE escape configuration
    _LIKE_ESCAPE_CLAUSE = "ESCAPE '\\'"
    
    # Status code ranges for filtering
    _STATUS_CODE_RANGES = {
        "2xx": (200, 299),
        "3xx": (300, 399),
        "4xx": (400, 499),
        "5xx": (500, 599),
    }

    def __init__(self, db: Database):
        self.db = db

    # ── Public write API ──────────────────────────────────────────────────────

    def save_history(
        self, request: Request, response: Optional[Response] = None, error: Optional[str] = None
    ) -> int:
        """Save request/response to history.

        Args:
            request: Request object
            response: Response object (if successful)
            error: Error message (if failed)

        Returns:
            History ID

        Raises:
            ValidationError: If input is invalid
            SecurityError: If limits exceeded
            StorageError: If save fails
        """
        logger.debug("save_history() called for %s %s", request.method, request.url[:60])
        self._prune_oldest_entry_if_limit_reached()

        sanitized_url = self._prepare_url(request.url)
        method = self._validate_method(request.method)
        request_headers_json = self._prepare_request_headers(request.headers or {})
        request_body = self._prepare_body(request.body)

        response_fields = self._extract_response_fields(response)
        if response_fields:
            status_code, reason, elapsed, response_headers_json, response_body = (
                response_fields.status_code,
                response_fields.reason,
                response_fields.elapsed,
                response_fields.headers_json,
                response_fields.body,
            )
        else:
            status_code = reason = elapsed = response_headers_json = response_body = None

        error = self._truncate_error(error)

        try:
            history_id = self.db.insert(
                """
                INSERT INTO history
                (request_id, method, url, status_code, reason, request_headers, request_body,
                 response_headers, response_body, elapsed, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    getattr(request, "id", None),
                    method,
                    sanitized_url,
                    status_code,
                    reason,
                    request_headers_json,
                    request_body,
                    response_headers_json,
                    response_body,
                    elapsed,
                    error,
                ),
            )
            logger.info(
                "Saved history entry id=%d: %s %s status=%s elapsed=%.2fs",
                history_id, method, sanitized_url, status_code or "error", elapsed or 0
            )
            try:
                # Populate normalized index for faster intelligent matching/search
                # Pass the raw response object where available so hashing uses raw bytes
                self._index_history_row(history_id, method, sanitized_url, status_code, response)
            except Exception as idx_exc:
                logger.debug("Failed to index history row %s: %s", history_id, idx_exc)
            return history_id

        except Exception as insert_exc:
            logger.error("Failed to save history entry: %s", insert_exc)
            # Preserve original traceback context for easier debugging
            raise StorageError(f"Failed to save history: {insert_exc}") from insert_exc

    def delete_history(self, history_id: int) -> None:
        """Delete a history entry by ID.

        Raises:
            ValidationError: If history_id is invalid
            StorageError: If entry doesn't exist or deletion fails
        """
        self._require_positive_int(history_id, "History ID")

        try:
            cursor = self.db.execute("DELETE FROM history WHERE id = ?", (history_id,))
            if cursor.rowcount == 0:
                raise StorageError(f"History entry with ID {history_id} does not exist")
            logger.info("Deleted history entry %d", history_id)
        except StorageError:
            raise
        except Exception as delete_exc:
            raise StorageError(f"Failed to delete history entry: {delete_exc}")

    def clear_history(self, days: Optional[int] = None) -> None:
        """Clear history, optionally keeping entries newer than *days* days.

        Args:
            days: If given, only delete entries older than this many days

        Raises:
            ValidationError: If days parameter is invalid
            StorageError: If deletion fails
        """
        if days is not None:
            self._require_positive_int(days, "Days")
            if days > 36500:
                raise ValidationError("Days value too large (max 36500)")

            try:
                count_row = self.db.fetchone(
                    "SELECT COUNT(*) as count FROM history "
                    "WHERE executed_at < datetime('now', '-' || ? || ' days')",
                    (days,),
                )
                count = count_row["count"] if count_row else 0
                self.db.execute(
                    "DELETE FROM history WHERE executed_at < datetime('now', '-' || ? || ' days')",
                    (days,),
                )
                logger.warning("Deleted %d history entries older than %d days", count, days)
            except Exception as exc:
                raise StorageError(f"Failed to clear old history: {exc}")
        else:
            try:
                count_row = self.db.fetchone("SELECT COUNT(*) as count FROM history")
                count = count_row["count"] if count_row else 0
                self.db.execute("DELETE FROM history")
                logger.warning("Cleared all %d history entries", count)
            except Exception as exc:
                raise StorageError(f"Failed to clear history: {exc}")

    # ── Public read API ───────────────────────────────────────────────────────

    def get_history(self, history_id: int) -> Optional[Dict[str, Any]]:
        """Get history entry by ID.

        Raises:
            ValidationError: If history_id is invalid
        """
        self._require_positive_int(history_id, "History ID")

        row = self.db.fetchone("SELECT * FROM history WHERE id = ?", (history_id,))
        if row:
            row = dict(row)
            self._decode_json_headers(row, history_id)
        return row

    def list_history(
        self, limit: int = 100, offset: int = 0, request_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List history entries.

        Args:
            limit: Maximum number of entries to return
            offset: Number of entries to skip
            request_id: Optional request ID to filter by

        Raises:
            ValidationError: If parameters are invalid
            SecurityError: If limits are exceeded
        """
        self._validate_pagination(limit, offset)

        if request_id is not None:
            self._require_positive_int(request_id, "Request ID")
            query = "SELECT * FROM history WHERE request_id = ? ORDER BY executed_at DESC LIMIT ? OFFSET ?"
            params = (request_id, limit, offset)
        else:
            query = "SELECT * FROM history ORDER BY executed_at DESC LIMIT ? OFFSET ?"
            params = (limit, offset)

        rows = self.db.fetchall(query, params)
        for row in rows:
            self._decode_json_headers(row, row["id"])
        return rows

    def search_history(
        self,
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
        """Filter history with SQL WHERE clauses and Python post-filters.

        **SQL-level filters** (fast, use indexes):

        * *query* – text to search in URL or request body (case-insensitive LIKE).
        * *method* – HTTP method (e.g. ``"GET"``).  Empty string = all.
        * *status_class* – ``"2xx"``, ``"3xx"``, ``"4xx"``, ``"5xx"``,
          ``"errors"`` or ``""`` (all).
        * *status_code* – exact integer status code (e.g. ``404``).
          Takes precedence over *status_class* when both are given.
        * *content_type* – substring match against the ``response_headers``
          ``Content-Type`` value (SQL LIKE).
        * *min_elapsed* / *max_elapsed* – response-time bounds in seconds.
        * *executed_after* / *executed_before* – ISO-8601 timestamp strings
          to restrict the time window (inclusive).

        **Python post-filters** (applied after SQL fetch):

        * *body_regex* – Python regex tested against the response body
          (case-insensitive).  Max 500 chars.
        * *jsonpath* – JSONPath expression (``jsonpath-ng`` syntax) that must
          match at least one node in the parsed JSON response body.
        * *jsonpath_value* – if given together with *jsonpath*, the first
          JSONPath match must equal this string.
        * *header* – ``"Name: value"`` substring match in response headers.

        Raises:
            ValidationError: On invalid limit/offset, bad regex, or bad JSONPath.
        """
        self._validate_pagination(limit, offset)

        compiled_regex = self._compile_body_regex(body_regex)
        parsed_jsonpath = self._parse_jsonpath(jsonpath)

        conditions, params_list = self._build_sql_filters(
            query=query,
            method=method,
            status_code=status_code,
            status_class=status_class,
            content_type=content_type,
            min_elapsed=min_elapsed,
            max_elapsed=max_elapsed,
            executed_after=executed_after,
            executed_before=executed_before,
        )

        needs_post_filter = bool(compiled_regex or parsed_jsonpath or header)
        fetch_limit = limit * 4 if needs_post_filter else limit

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM history {where_clause} ORDER BY executed_at DESC LIMIT ? OFFSET ?"
        params_list.extend([fetch_limit, offset])

        rows = self.db.fetchall(sql, tuple(params_list))

        for row in rows:
            self._decode_json_headers(row)  # No row_id = silent mode

        if needs_post_filter:
            rows = self._apply_post_filters(
                rows,
                compiled_regex=compiled_regex,
                parsed_jsonpath=parsed_jsonpath,
                jsonpath_value=jsonpath_value,
                header=header,
                limit=limit,
            )

        return rows

    def get_stats(self) -> Dict[str, Any]:
        """Return aggregate history statistics."""
        total = self.db.fetchone("SELECT COUNT(*) as count FROM history")
        successful = self.db.fetchone(
            "SELECT COUNT(*) as count FROM history "
            "WHERE status_code IS NOT NULL AND status_code < 400"
        )
        failed = self.db.fetchone(
            "SELECT COUNT(*) as count FROM history "
            "WHERE status_code >= 400 OR error IS NOT NULL"
        )
        return {
            "total": total["count"] if total else 0,
            "successful": successful["count"] if successful else 0,
            "failed": failed["count"] if failed else 0,
        }

    # ── save_history helpers ──────────────────────────────────────────────────

    def _prune_oldest_entry_if_limit_reached(self) -> None:
        """Delete the oldest entries when the history cap is reached.

        Removes ~1% of the cap in one batch to avoid running a DELETE on
        every single insert once the table is at capacity.
        """
        count_row = self.db.fetchone("SELECT COUNT(*) as count FROM history")
        if count_row and count_row["count"] >= self.MAX_HISTORY_ENTRIES:
            prune_count = max(1, self.MAX_HISTORY_ENTRIES // 100)
            logger.warning(
                "History limit reached (%d), removing %d oldest entries",
                self.MAX_HISTORY_ENTRIES, prune_count,
            )
            self.db.execute(
                "DELETE FROM history WHERE id IN "
                "(SELECT id FROM history ORDER BY executed_at ASC LIMIT ?)",
                (prune_count,),
            )

    def _prepare_url(self, url: str) -> str:
        """Validate, truncate, and redact sensitive query parameters from a URL."""
        if not isinstance(url, str):
            raise ValidationError("Request URL must be a string")

        if len(url) > self.MAX_URL_LENGTH:
            logger.warning("URL too long, truncating: %s...", url[:100])
            url = url[:self.MAX_URL_LENGTH]

        sanitized = redact_url(url)
        if sanitized != url:
            logger.info("Sensitive data detected and redacted from URL in history")
        return sanitized

    def _validate_method(self, method: str) -> str:
        """Validate that method is a string."""
        if not isinstance(method, str):
            raise ValidationError("Request method must be a string")
        return method

    def _prepare_request_headers(self, headers: Dict[str, Any]) -> str:
        """Sanitize and serialize request headers to JSON.

        Raises:
            ValidationError: If headers is not a dict
            SecurityError: If the serialized headers exceed the size limit
        """
        if not isinstance(headers, dict):
            raise ValidationError("Request headers must be a dictionary")

        sanitized = redact_headers(headers)
        headers_json = safe_json_dumps(sanitized, max_len=self.MAX_HEADERS_SIZE)
        return headers_json

    def _prepare_body(self, body: Any) -> Optional[str]:
        """Coerce and truncate a request/response body to a storable string."""
        if body is None:
            return None

        if not isinstance(body, str):
            body = str(body)

        if len(body) > self.MAX_BODY_SIZE:
            logger.warning(
                "Request body too large, truncating from %d to %d bytes",
                len(body), self.MAX_BODY_SIZE,
            )
            body = body[:self.MAX_BODY_SIZE] + "... [TRUNCATED]"

        return body

    def _extract_response_fields(
        self, response: Optional[Response]
    ) -> Optional[ResponseFields]:
        """Extract storable scalar fields from a Response object.

        Returns:
            ResponseFields with status_code, reason, elapsed, headers_json, body,
            or None if response is None
        """
        if response is None:
            return None

        status_code = response.status_code
        reason = response.reason
        elapsed = response.elapsed

        response_headers = dict(response.headers) if response.headers else {}
        sanitized_response_headers = redact_headers(response_headers)
        try:
            response_headers_json = safe_json_dumps(
                sanitized_response_headers, max_len=self.MAX_HEADERS_SIZE
            )
        except SecurityError:
            logger.warning("Response headers too large, storing truncated version")
            # Best-effort truncated representation
            response_headers_json = safe_json_dumps(sanitized_response_headers)[:self.MAX_HEADERS_SIZE] + "..."

        response_body = self._decode_body(response.body)
        response_body = self._prepare_body(response_body)

        return ResponseFields(
            status_code=status_code,
            reason=reason,
            elapsed=elapsed,
            headers_json=response_headers_json,
            body=response_body,
        )

    @staticmethod
    def _decode_body(body: Any, strict: bool = False) -> Optional[str]:
        """Decode a response/request body (bytes or str) to a string.
        
        Args:
            body: The body to decode (bytes, str, or None)
            strict: If True, raise on decode error; if False, return empty string on error
            
        Returns:
            Decoded string, None (if body is None and strict=False), or empty string on error
            
        Raises:
            UnicodeDecodeError: If strict=True and decoding fails
        """
        # Delegate to module-level coercion helper for a single canonical
        # implementation used across the module.
        return coerce_body_to_str(body, strict=strict)

    def _truncate_error(self, error: Optional[str]) -> Optional[str]:
        """Coerce and truncate an error message string."""
        if error is None:
            return None
        if not isinstance(error, str):
            error = str(error)
        if len(error) > self.MAX_ERROR_MESSAGE_LENGTH:
            error = error[:self.MAX_ERROR_MESSAGE_LENGTH] + "... [TRUNCATED]"
        return error

    # ── JSON header decoding ──────────────────────────────────────────────────

    def _decode_json_headers(self, row: Dict[str, Any], row_id: Optional[int] = None) -> None:
        """Decode request_headers and response_headers from JSON strings in-place.
        
        Args:
            row: History row dict to modify in-place
            row_id: Optional ID for error logging; if None, errors are silently ignored
        """
        for col in ("request_headers", "response_headers"):
            if not row.get(col):
                row[col] = {}
                continue
            try:
                row[col] = safe_json_loads(row[col], row_id=row_id)
            except Exception:
                # _safe_json_loads already logs; fall back to empty dict
                row[col] = {}

    # ── Validation helpers ────────────────────────────────────────────────────

    @staticmethod
    def _require_positive_int(value: Any, label: str) -> None:
        """Raise ValidationError unless value is a positive integer."""
        _require_positive_int_impl(value, label)

    def _validate_pagination(self, limit: int, offset: int) -> None:
        """Validate limit/offset pagination parameters."""
        if not isinstance(limit, int) or limit <= 0:
            raise ValidationError("Limit must be a positive integer")
        if limit > self.MAX_LIMIT:
            raise SecurityError(f"Limit too large (max {self.MAX_LIMIT})")
        if not isinstance(offset, int) or offset < 0:
            raise ValidationError("Offset must be a non-negative integer")

    # ── Search helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _validate_iso_timestamp(timestamp: str, label: str) -> None:
        """Validate that a timestamp string is in ISO-8601 format.
        
        Raises:
            ValidationError: If the timestamp is not in valid ISO-8601 format
        """
        if not isinstance(timestamp, str):
            raise ValidationError(f"{label} must be a string")
        
        # Allow common ISO-8601 formats:
        # - 2026-03-23
        # - 2026-03-23T16:20:00
        # - 2026-03-23T16:20:00Z
        # - 2026-03-23T16:20:00+00:00
        import datetime
        formats = [
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
        ]
        
        for fmt in formats:
            try:
                datetime.datetime.strptime(timestamp, fmt)
                return
            except ValueError:
                continue
        
        raise ValidationError(
            f"{label} must be in ISO-8601 format (e.g., 2026-03-23T16:20:00Z)"
        )

    def _escape_like(self, text: str) -> str:
        r"""Escape SQL LIKE metacharacters (``%``, ``_``, ``\``) so they
        match literally when used with ``ESCAPE '\'``."""
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _compile_body_regex(self, body_regex: str) -> Optional["re.Pattern[str]"]:
        """Compile and return a body regex, or None if body_regex is empty.

        Raises:
            ValidationError: If the pattern is too long or syntactically invalid.
        """
        if not body_regex:
            return None
        if len(body_regex) > self.MAX_REGEX_LENGTH:
            raise ValidationError(
                f"Regex pattern too long (max {self.MAX_REGEX_LENGTH} chars)"
            )
        try:
            return re.compile(body_regex, re.IGNORECASE)
        except re.error as regex_exc:
            raise ValidationError(f"Invalid regex pattern: {regex_exc}")

    @staticmethod
    def _parse_jsonpath(jsonpath: str) -> Optional[Any]:
        """Parse and return a JSONPath expression, or None if empty.

        Raises:
            ValidationError: If the expression is syntactically invalid.
        """
        if not jsonpath:
            return None
        try:
            from jsonpath_ng import parse as _jp_parse
            return _jp_parse(jsonpath)
        except Exception as jp_exc:
            raise ValidationError(f"Invalid JSONPath expression: {jp_exc}")

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
    ):
        """Return (conditions_list, params_list) for the WHERE clause."""
        conditions: List[str] = []
        params: List[Any] = []

        if query and isinstance(query, str):
            escaped = self._escape_like(query)
            like = f"%{escaped}%"
            conditions.append(f"(url LIKE ? {self._LIKE_ESCAPE_CLAUSE} OR request_body LIKE ? {self._LIKE_ESCAPE_CLAUSE})")
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
            if status_class_lower in self._STATUS_CODE_RANGES:
                start, end = self._STATUS_CODE_RANGES[status_class_lower]
                conditions.append(f"status_code BETWEEN {start} AND {end}")
            elif status_class_lower == "errors":
                conditions.append("(status_code IS NULL OR status_code >= 400)")

        if content_type and isinstance(content_type, str):
            escaped_ct = self._escape_like(content_type)
            conditions.append(f"response_headers LIKE ? {self._LIKE_ESCAPE_CLAUSE}")
            params.append(f"%{escaped_ct}%")

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

    @staticmethod
    def _apply_post_filters(
        rows: List[Dict[str, Any]],
        *,
        compiled_regex: Optional["re.Pattern[str]"] = None,
        parsed_jsonpath: Optional[Any] = None,
        jsonpath_value: Optional[str] = None,
        header: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Apply Python-side filters that cannot be expressed in SQL.
        
        Filters rows by regex body match, JSONPath match, and/or header match,
        returning up to `limit` matching rows.
        """
        result: List[Dict[str, Any]] = []
        header_name, header_val = HistoryManager._parse_header_filter(header)

        for row in rows:
            if len(result) >= limit:
                break

            # Apply all active filters (all must pass)
            if compiled_regex and not HistoryManager._matches_body_regex(row, compiled_regex):
                continue
            if parsed_jsonpath and not HistoryManager._matches_jsonpath(row, parsed_jsonpath, jsonpath_value):
                continue
            if header_name and not HistoryManager._matches_header(row, header_name, header_val):
                continue

            result.append(row)

        return result

    @staticmethod
    def _matches_body_regex(row: Dict[str, Any], compiled_regex: "re.Pattern[str]") -> bool:
        """Return True if the response body matches the regex pattern."""
        body = coerce_body_to_str(row.get("response_body") or "") or ""
        return bool(compiled_regex.search(body))

    @staticmethod
    def _matches_jsonpath(
        row: Dict[str, Any],
        parsed_jsonpath: Any,
        jsonpath_value: Optional[str] = None,
    ) -> bool:
        """Return True if the response body contains a JSONPath match.
        
        If jsonpath_value is given, the first match must equal that value.
        """
        body = coerce_body_to_str(row.get("response_body") or "") or ""
        # Parse JSON body for JSONPath matching. Use the safe loader so
        # a corrupted JSON string doesn't raise; treat parse-failure as no-match.
        try:
            data = safe_json_loads(body)
            # safe_json_loads returns {} on failure; if body is non-empty and
            # parsing produced an empty dict, treat as parse failure to preserve
            # previous behavior (which returned False on JSONDecodeError).
            if body and body.strip() and data == {}:
                return False
        except Exception:
            return False

        matches = parsed_jsonpath.find(data)
        if not matches:
            return False
        
        if jsonpath_value is not None:
            return str(matches[0].value) == jsonpath_value
        
        return True

    @staticmethod
    def _matches_header(
        row: Dict[str, Any],
        header_name: str,
        header_val: str = "",
    ) -> bool:
        """Return True if any response header matches the name/value filter."""
        resp_headers = row.get("response_headers") or {}
        if isinstance(resp_headers, str):
            # Use safe loader to avoid noisy exceptions on malformed DB values
            resp_headers = safe_json_loads(resp_headers, row_id=row.get("id"))
            if not isinstance(resp_headers, dict):
                resp_headers = {}
        
        for key, value in resp_headers.items():
            if header_name in key.lower():
                if not header_val or header_val in str(value).lower():
                    return True
        return False

    @staticmethod
    def _parse_header_filter(header: str) -> Tuple[str, str]:
        """Parse 'Name: value' header filter into (name, value) components.
        
        Returns:
            (header_name, header_value) both lowercased, or ('', '') if header is empty
        """
        if not header:
            return "", ""
        
        if ":" in header:
            name, val = header.split(":", 1)
            return name.strip().lower(), val.strip().lower()
        
        return header.strip().lower(), ""

    def _index_history_row(
        self,
        history_id: int,
        method: str,
        url: str,
        status_code: Optional[int],
        response_obj: Any,
    ) -> None:
        """Create or update a normalized index row for a history entry.

        This method intentionally swallows transient errors so indexing is
        best-effort and does not prevent history writes.
        """
        try:
            # Ensure placeholders are expanded (best-effort) before indexing
            expanded_url = urls.expand_placeholders(url, None)
            parts = urls.normalized_parts(expanded_url)
            normalized_url = parts.get("normalized_url")
            path_segments = parts.get("path_segments") or []
            query_params = parts.get("query_params") or {}

            # Guard index payload sizes to avoid storing huge JSON blobs
            if len(path_segments) > MAX_INDEX_PATH_SEGMENTS:
                path_segments = path_segments[:MAX_INDEX_PATH_SEGMENTS]
            if isinstance(query_params, dict) and len(query_params) > MAX_INDEX_QUERY_PARAMS:
                # Keep only the first N keys deterministically
                limited = {}
                for i, (k, v) in enumerate(query_params.items()):
                    if i >= MAX_INDEX_QUERY_PARAMS:
                        break
                    limited[k] = v
                query_params = limited

            # Body hash for quick comparisons. Prefer raw response bytes when
            # available (keeps hashing deterministic even when stored body is truncated).
            body_hash = None
            resp = response_obj
            if resp is not None:
                # Try to extract raw bytes from a Response-like object
                raw = None
                try:
                    if hasattr(resp, "body") and isinstance(resp.body, (bytes, bytearray)):
                        raw = bytes(resp.body)
                    elif hasattr(resp, "content") and isinstance(resp.content, (bytes, bytearray)):
                        raw = bytes(resp.content)
                    else:
                        # Fallback to provided object/string representation
                        raw = str(resp).encode("utf-8")
                except Exception:
                    raw = None

                if raw is not None:
                    body_hash = hashlib.sha256(raw).hexdigest()

            response_success = 1 if (isinstance(status_code, int) and 200 <= status_code < 300) else 0

            executed_at_row = self.db.fetchone("SELECT executed_at FROM history WHERE id = ?", (history_id,))
            executed_at = executed_at_row.get("executed_at") if executed_at_row else None

            self.db.insert(
                """
                INSERT INTO history_index
                (history_id, method, normalized_url, path_segments, query_params, body_hash, response_success, executed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_id,
                    method,
                    normalized_url,
                    # Serialize path_segments / query_params using safe dumps
                    # with conservative size limits to avoid bloating the index.
                    (lambda obj: safe_json_dumps(obj, max_len=4096))(path_segments),
                    (lambda obj: safe_json_dumps(obj, max_len=8192))(query_params),
                    body_hash,
                    response_success,
                    executed_at,
                ),
            )
        except Exception as exc:
            # Best-effort indexing — do not raise
            logger.debug("Indexing history row failed: %s", exc)



# module helpers moved to equinox.storage.utils
