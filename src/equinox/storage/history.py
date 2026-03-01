"""Request history management"""

import json
import logging
import re
from typing import List, Dict, Any, Optional

from equinox.storage.database import Database
from equinox.core.request import Request, Response
from equinox.core.exceptions import StorageError, ValidationError, SecurityError

logger = logging.getLogger(__name__)


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

    SENSITIVE_PATTERNS = [
        re.compile(r'(password|passwd|pwd)=([^&\s]+)', re.IGNORECASE),
        re.compile(r'(token|api[_-]?key|secret)=([^&\s]+)', re.IGNORECASE),
        re.compile(r'(auth|authorization)=([^&\s]+)', re.IGNORECASE),
    ]

    _SENSITIVE_HEADER_KEYS = {
        'authorization', 'x-api-key', 'api-key', 'apikey',
        'token', 'x-auth-token', 'x-access-token',
        'cookie', 'set-cookie', 'x-csrf-token',
        'password', 'secret',
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
        self._prune_oldest_entry_if_limit_reached()

        sanitized_url = self._prepare_url(request.url)
        method = self._validate_method(request.method)
        request_headers_json = self._prepare_request_headers(request.headers or {})
        request_body = self._prepare_body(request.body)

        status_code, reason, elapsed, response_headers_json, response_body = (
            self._extract_response_fields(response)
        )

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
            logger.debug("Saved history entry %d for %s %s", history_id, method, sanitized_url)
            return history_id

        except Exception as insert_exc:
            raise StorageError(f"Failed to save history: {insert_exc}")

    def delete_history(self, history_id: int) -> None:
        """Delete a history entry by ID.

        Raises:
            ValidationError: If history_id is invalid
            StorageError: If entry doesn't exist or deletion fails
        """
        self._require_positive_int(history_id, "History ID")

        if not self.get_history(history_id):
            raise StorageError(f"History entry with ID {history_id} does not exist")

        try:
            self.db.execute("DELETE FROM history WHERE id = ?", (history_id,))
            logger.info("Deleted history entry %d", history_id)
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
            self._decode_json_header_columns(row, history_id)
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
            self._decode_json_header_columns(row, row["id"])
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
            self._decode_json_header_columns_silent(row)

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
        """Delete the single oldest history entry when the cap is reached."""
        count_row = self.db.fetchone("SELECT COUNT(*) as count FROM history")
        if count_row and count_row["count"] >= self.MAX_HISTORY_ENTRIES:
            logger.warning(
                "History limit reached (%d), removing oldest entry", self.MAX_HISTORY_ENTRIES
            )
            self.db.execute(
                "DELETE FROM history WHERE id = "
                "(SELECT id FROM history ORDER BY executed_at ASC LIMIT 1)"
            )

    def _prepare_url(self, url: str) -> str:
        """Validate, truncate, and redact sensitive query parameters from a URL."""
        if not isinstance(url, str):
            raise ValidationError("Request URL must be a string")

        if len(url) > self.MAX_URL_LENGTH:
            logger.warning("URL too long, truncating: %s...", url[:100])
            url = url[:self.MAX_URL_LENGTH]

        sanitized = self._sanitize_url(url)
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

        sanitized = self._sanitize_headers(headers)
        headers_json = json.dumps(sanitized)

        if len(headers_json) > self.MAX_HEADERS_SIZE:
            raise SecurityError(
                f"Request headers too large (max {self.MAX_HEADERS_SIZE} bytes)"
            )
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
    ):
        """Extract storable scalar fields from a Response object.

        Returns:
            (status_code, reason, elapsed, response_headers_json, response_body)
        """
        if response is None:
            return None, None, None, None, None

        status_code = response.status_code
        reason = response.reason
        elapsed = response.elapsed

        response_headers = dict(response.headers) if response.headers else {}
        sanitized_response_headers = self._sanitize_headers(response_headers)
        response_headers_json = json.dumps(sanitized_response_headers)

        if len(response_headers_json) > self.MAX_HEADERS_SIZE:
            logger.warning("Response headers too large, storing truncated version")
            response_headers_json = response_headers_json[:self.MAX_HEADERS_SIZE] + "..."

        response_body = self._decode_response_body(response.body)
        response_body = self._prepare_body(response_body)

        return status_code, reason, elapsed, response_headers_json, response_body

    @staticmethod
    def _decode_response_body(body: Any) -> Optional[str]:
        """Decode a bytes or string response body to a UTF-8 string."""
        if body is None:
            return None
        if isinstance(body, bytes):
            try:
                return body.decode("utf-8", errors="replace")
            except Exception:
                return body.decode("latin-1")
        if not isinstance(body, str):
            return str(body)
        return body

    def _truncate_error(self, error: Optional[str]) -> Optional[str]:
        """Coerce and truncate an error message string."""
        if error is None:
            return None
        if not isinstance(error, str):
            error = str(error)
        if len(error) > self.MAX_ERROR_MESSAGE_LENGTH:
            error = error[:self.MAX_ERROR_MESSAGE_LENGTH] + "... [TRUNCATED]"
        return error

    # ── Header/URL sanitisation ───────────────────────────────────────────────

    def _sanitize_url(self, url: str) -> str:
        """Redact sensitive query parameter values in a URL."""
        sanitized = url
        for pattern in self.SENSITIVE_PATTERNS:
            sanitized = pattern.sub(r'\1=[REDACTED]', sanitized)
        return sanitized

    def _sanitize_headers(self, headers: Dict[str, Any]) -> Dict[str, Any]:
        """Redact values of security-sensitive HTTP headers."""
        return {
            key: ("[REDACTED]" if any(s in key.lower() for s in self._SENSITIVE_HEADER_KEYS) else value)
            for key, value in headers.items()
        }

    # ── JSON header decoding ──────────────────────────────────────────────────

    def _decode_json_header_columns(self, row: Dict[str, Any], row_id: int) -> None:
        """Decode request_headers and response_headers columns in-place, logging errors."""
        try:
            row["request_headers"] = json.loads(row["request_headers"])
        except (json.JSONDecodeError, TypeError) as parse_exc:
            logger.error(
                "Failed to parse request headers for history %d: %s", row_id, parse_exc
            )
            row["request_headers"] = {}

        if row.get("response_headers"):
            try:
                row["response_headers"] = json.loads(row["response_headers"])
            except (json.JSONDecodeError, TypeError) as parse_exc:
                logger.error(
                    "Failed to parse response headers for history %d: %s", row_id, parse_exc
                )
                row["response_headers"] = {}

    @staticmethod
    def _decode_json_header_columns_silent(row: Dict[str, Any]) -> None:
        """Decode JSON header columns in-place, silently falling back to empty dicts."""
        try:
            row["request_headers"] = json.loads(row["request_headers"] or "{}")
        except (json.JSONDecodeError, TypeError):
            row["request_headers"] = {}

        if row.get("response_headers"):
            try:
                row["response_headers"] = json.loads(row["response_headers"])
            except (json.JSONDecodeError, TypeError):
                row["response_headers"] = {}

    # ── Validation helpers ────────────────────────────────────────────────────

    @staticmethod
    def _require_positive_int(value: Any, label: str) -> None:
        """Raise ValidationError unless value is a positive integer."""
        if not isinstance(value, int) or value <= 0:
            raise ValidationError(f"{label} must be a positive integer")

    def _validate_pagination(self, limit: int, offset: int) -> None:
        """Validate limit/offset pagination parameters."""
        if not isinstance(limit, int) or limit <= 0:
            raise ValidationError("Limit must be a positive integer")
        if limit > self.MAX_LIMIT:
            raise SecurityError(f"Limit too large (max {self.MAX_LIMIT})")
        if not isinstance(offset, int) or offset < 0:
            raise ValidationError("Offset must be a non-negative integer")

    # ── Search helpers ────────────────────────────────────────────────────────

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

    @staticmethod
    def _build_sql_filters(
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
            like = f"%{query}%"
            conditions.append("(url LIKE ? OR request_body LIKE ?)")
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
            if status_class_lower == "2xx":
                conditions.append("status_code BETWEEN 200 AND 299")
            elif status_class_lower == "3xx":
                conditions.append("status_code BETWEEN 300 AND 399")
            elif status_class_lower == "4xx":
                conditions.append("status_code BETWEEN 400 AND 499")
            elif status_class_lower == "5xx":
                conditions.append("status_code BETWEEN 500 AND 599")
            elif status_class_lower == "errors":
                conditions.append("(status_code IS NULL OR status_code >= 400)")

        if content_type and isinstance(content_type, str):
            conditions.append("response_headers LIKE ?")
            params.append(f"%{content_type}%")

        if min_elapsed is not None:
            conditions.append("elapsed >= ?")
            params.append(float(min_elapsed))
        if max_elapsed is not None:
            conditions.append("elapsed <= ?")
            params.append(float(max_elapsed))

        if executed_after and isinstance(executed_after, str):
            conditions.append("executed_at >= ?")
            params.append(executed_after)
        if executed_before and isinstance(executed_before, str):
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
        """Apply Python-side filters that cannot be expressed in SQL."""
        result: List[Dict[str, Any]] = []

        header_name = header_val = ""
        if header:
            if ":" in header:
                header_name, header_val = header.split(":", 1)
                header_name = header_name.strip().lower()
                header_val = header_val.strip().lower()
            else:
                header_name = header.strip().lower()

        for row in rows:
            if len(result) >= limit:
                break

            if compiled_regex:
                body = _decode_bytes_body(row.get("response_body") or "")
                if not compiled_regex.search(body):
                    continue

            if parsed_jsonpath:
                body = _decode_bytes_body(row.get("response_body") or "")
                try:
                    data = json.loads(body)
                except (json.JSONDecodeError, TypeError):
                    continue
                matches = parsed_jsonpath.find(data)
                if not matches:
                    continue
                if jsonpath_value is not None and str(matches[0].value) != jsonpath_value:
                    continue

            if header_name:
                resp_headers = row.get("response_headers") or {}
                if isinstance(resp_headers, str):
                    try:
                        resp_headers = json.loads(resp_headers)
                    except (json.JSONDecodeError, TypeError):
                        resp_headers = {}
                if not _header_matches(resp_headers, header_name, header_val):
                    continue

            result.append(row)

        return result


# ── Module-level helpers ──────────────────────────────────────────────────────


def _decode_bytes_body(body: Any) -> str:
    """Coerce a bytes or str body to str for regex/JSON matching."""
    if isinstance(body, bytes):
        try:
            return body.decode("utf-8", errors="replace")
        except Exception:
            return ""
    return body or ""


def _header_matches(headers: Dict[str, Any], header_name: str, header_val: str) -> bool:
    """Return True if any header key contains header_name and its value contains header_val."""
    for key, value in headers.items():
        if header_name in key.lower():
            if not header_val or header_val in str(value).lower():
                return True
    return False
