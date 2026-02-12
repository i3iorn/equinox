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

    # Security limits
    MAX_HISTORY_ENTRIES = 100000  # Maximum total history entries
    MAX_BODY_SIZE = 10 * 1024 * 1024  # 10MB max body size to store
    MAX_HEADERS_SIZE = 100 * 1024  # 100KB max headers size
    MAX_URL_LENGTH = 2048
    MAX_ERROR_MESSAGE_LENGTH = 10000
    DEFAULT_LIMIT = 100
    MAX_LIMIT = 10000  # Maximum entries per query

    # Patterns for detecting sensitive data in URLs
    SENSITIVE_PATTERNS = [
        re.compile(r'(password|passwd|pwd)=([^&\s]+)', re.IGNORECASE),
        re.compile(r'(token|api[_-]?key|secret)=([^&\s]+)', re.IGNORECASE),
        re.compile(r'(auth|authorization)=([^&\s]+)', re.IGNORECASE),
    ]

    def __init__(self, db: Database):
        """
        Initialize history manager

        Args:
            db: Database instance
        """
        self.db = db

    def save_history(
        self, request: Request, response: Optional[Response] = None, error: Optional[str] = None
    ) -> int:
        """
        Save request/response to history

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
        # Check history entry count limit
        count = self.db.fetchone("SELECT COUNT(*) as count FROM history")
        if count and count["count"] >= self.MAX_HISTORY_ENTRIES:
            logger.warning(f"History limit reached ({self.MAX_HISTORY_ENTRIES}), oldest entries will be pruned")
            # Delete oldest 10% to make room
            delete_count = self.MAX_HISTORY_ENTRIES // 10
            self.db.execute(
                f"DELETE FROM history WHERE id IN (SELECT id FROM history ORDER BY executed_at ASC LIMIT {delete_count})"
            )

        # Validate and sanitize URL
        url = request.url
        if not isinstance(url, str):
            raise ValidationError("Request URL must be a string")

        if len(url) > self.MAX_URL_LENGTH:
            logger.warning(f"URL too long, truncating: {url[:100]}...")
            url = url[:self.MAX_URL_LENGTH]

        # Sanitize URL - remove sensitive data from query parameters
        sanitized_url = self._sanitize_url(url)
        if sanitized_url != url:
            logger.info("Sensitive data detected and redacted from URL in history")

        # Validate method
        method = request.method
        if not isinstance(method, str):
            raise ValidationError("Request method must be a string")

        # Sanitize and validate headers
        request_headers = request.headers or {}
        if not isinstance(request_headers, dict):
            raise ValidationError("Request headers must be a dictionary")

        sanitized_request_headers = self._sanitize_headers(request_headers)
        request_headers_json = json.dumps(sanitized_request_headers)

        if len(request_headers_json) > self.MAX_HEADERS_SIZE:
            raise SecurityError(f"Request headers too large (max {self.MAX_HEADERS_SIZE} bytes)")

        # Sanitize and validate request body
        request_body = request.body
        if request_body is not None:
            if not isinstance(request_body, str):
                request_body = str(request_body)

            if len(request_body) > self.MAX_BODY_SIZE:
                logger.warning(f"Request body too large, truncating from {len(request_body)} to {self.MAX_BODY_SIZE} bytes")
                request_body = request_body[:self.MAX_BODY_SIZE] + "... [TRUNCATED]"

        # Process response if present
        response_headers_json = None
        response_body = None
        status_code = None
        reason = None
        elapsed = None

        if response:
            status_code = response.status_code
            reason = response.reason
            elapsed = response.elapsed

            # Sanitize response headers
            response_headers = dict(response.headers) if response.headers else {}
            sanitized_response_headers = self._sanitize_headers(response_headers)
            response_headers_json = json.dumps(sanitized_response_headers)

            if len(response_headers_json) > self.MAX_HEADERS_SIZE:
                logger.warning("Response headers too large, storing truncated version")
                response_headers_json = response_headers_json[:self.MAX_HEADERS_SIZE] + "..."

            # Validate response body
            response_body = response.body
            if response_body is not None:
                if not isinstance(response_body, str):
                    response_body = str(response_body)

                if len(response_body) > self.MAX_BODY_SIZE:
                    logger.warning(f"Response body too large, truncating from {len(response_body)} to {self.MAX_BODY_SIZE} bytes")
                    response_body = response_body[:self.MAX_BODY_SIZE] + "... [TRUNCATED]"

        # Validate error message
        if error is not None:
            if not isinstance(error, str):
                error = str(error)

            if len(error) > self.MAX_ERROR_MESSAGE_LENGTH:
                error = error[:self.MAX_ERROR_MESSAGE_LENGTH] + "... [TRUNCATED]"

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
            logger.debug(f"Saved history entry {history_id} for {method} {sanitized_url}")
            return history_id

        except Exception as e:
            raise StorageError(f"Failed to save history: {e}")

    def _sanitize_url(self, url: str) -> str:
        """Remove sensitive data from URL query parameters.

        Args:
            url: URL to sanitize

        Returns:
            Sanitized URL
        """
        sanitized = url
        for pattern in self.SENSITIVE_PATTERNS:
            # Replace sensitive values with [REDACTED]
            sanitized = pattern.sub(r'\1=[REDACTED]', sanitized)
        return sanitized

    def _sanitize_headers(self, headers: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive data from headers.

        Args:
            headers: Headers dictionary

        Returns:
            Sanitized headers dictionary
        """
        sanitized = {}
        sensitive_keys = {
            'authorization', 'x-api-key', 'api-key', 'apikey',
            'token', 'x-auth-token', 'x-access-token',
            'cookie', 'set-cookie', 'x-csrf-token',
            'password', 'secret'
        }

        for key, value in headers.items():
            key_lower = key.lower()
            if any(sensitive in key_lower for sensitive in sensitive_keys):
                # Redact sensitive header values
                sanitized[key] = "[REDACTED]"
            else:
                # Keep non-sensitive headers
                sanitized[key] = value

        return sanitized

    def get_history(self, history_id: int) -> Optional[Dict[str, Any]]:
        """
        Get history entry by ID

        Args:
            history_id: History ID

        Returns:
            History data or None

        Raises:
            ValidationError: If history_id is invalid
        """
        if not isinstance(history_id, int) or history_id <= 0:
            raise ValidationError("History ID must be a positive integer")

        row = self.db.fetchone("SELECT * FROM history WHERE id = ?", (history_id,))
        if row:
            row = dict(row)
            try:
                row["request_headers"] = json.loads(row["request_headers"])
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to parse request headers for history {history_id}: {e}")
                row["request_headers"] = {}

            if row["response_headers"]:
                try:
                    row["response_headers"] = json.loads(row["response_headers"])
                except (json.JSONDecodeError, TypeError) as e:
                    logger.error(f"Failed to parse response headers for history {history_id}: {e}")
                    row["response_headers"] = {}
        return row

    def list_history(
        self, limit: int = 100, offset: int = 0, request_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        List history entries

        Args:
            limit: Maximum number of entries to return
            offset: Number of entries to skip
            request_id: Optional request ID to filter by

        Returns:
            List of history entries

        Raises:
            ValidationError: If parameters are invalid
            SecurityError: If limits are exceeded
        """
        # Validate limit
        if not isinstance(limit, int) or limit <= 0:
            raise ValidationError("Limit must be a positive integer")

        if limit > self.MAX_LIMIT:
            raise SecurityError(f"Limit too large (max {self.MAX_LIMIT})")

        # Validate offset
        if not isinstance(offset, int) or offset < 0:
            raise ValidationError("Offset must be a non-negative integer")

        # Validate request_id if provided
        if request_id is not None:
            if not isinstance(request_id, int) or request_id <= 0:
                raise ValidationError("Request ID must be a positive integer")

            query = "SELECT * FROM history WHERE request_id = ? ORDER BY executed_at DESC LIMIT ? OFFSET ?"
            params = (request_id, limit, offset)
        else:
            query = "SELECT * FROM history ORDER BY executed_at DESC LIMIT ? OFFSET ?"
            params = (limit, offset)

        rows = self.db.fetchall(query, params)
        for row in rows:
            try:
                row["request_headers"] = json.loads(row["request_headers"])
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to parse request headers for history {row['id']}: {e}")
                row["request_headers"] = {}

            if row["response_headers"]:
                try:
                    row["response_headers"] = json.loads(row["response_headers"])
                except (json.JSONDecodeError, TypeError) as e:
                    logger.error(f"Failed to parse response headers for history {row['id']}: {e}")
                    row["response_headers"] = {}

        return rows

    def delete_history(self, history_id: int) -> None:
        """Delete history entry

        Args:
            history_id: History ID to delete

        Raises:
            ValidationError: If history_id is invalid
            StorageError: If history entry doesn't exist or deletion fails
        """
        # Validate history_id
        if not isinstance(history_id, int) or history_id <= 0:
            raise ValidationError("History ID must be a positive integer")

        # Check history entry exists
        history = self.get_history(history_id)
        if not history:
            raise StorageError(f"History entry with ID {history_id} does not exist")

        try:
            self.db.execute("DELETE FROM history WHERE id = ?", (history_id,))
            logger.info(f"Deleted history entry {history_id}")

        except Exception as e:
            raise StorageError(f"Failed to delete history entry: {e}")

    def clear_history(self, days: Optional[int] = None) -> None:
        """
        Clear history

        Args:
            days: If specified, only delete history older than this many days

        Raises:
            ValidationError: If days parameter is invalid
            StorageError: If deletion fails
        """
        if days is not None:
            # Validate days parameter
            if not isinstance(days, int) or days <= 0:
                raise ValidationError("Days must be a positive integer")

            if days > 36500:  # 100 years
                raise ValidationError("Days value too large (max 36500)")

            try:
                # Count entries to be deleted
                count_result = self.db.fetchone(
                    "SELECT COUNT(*) as count FROM history WHERE executed_at < datetime('now', '-' || ? || ' days')",
                    (days,)
                )
                count = count_result["count"] if count_result else 0

                self.db.execute(
                    "DELETE FROM history WHERE executed_at < datetime('now', '-' || ? || ' days')",
                    (days,),
                )
                logger.warning(f"Deleted {count} history entries older than {days} days")

            except Exception as e:
                raise StorageError(f"Failed to clear old history: {e}")
        else:
            try:
                # Count total entries
                count_result = self.db.fetchone("SELECT COUNT(*) as count FROM history")
                count = count_result["count"] if count_result else 0

                self.db.execute("DELETE FROM history")
                logger.warning(f"Cleared all {count} history entries")

            except Exception as e:
                raise StorageError(f"Failed to clear history: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get history statistics"""
        total = self.db.fetchone("SELECT COUNT(*) as count FROM history")
        successful = self.db.fetchone(
            "SELECT COUNT(*) as count FROM history WHERE status_code IS NOT NULL AND status_code < 400"
        )
        failed = self.db.fetchone(
            "SELECT COUNT(*) as count FROM history WHERE status_code >= 400 OR error IS NOT NULL"
        )

        return {
            "total": total["count"] if total else 0,
            "successful": successful["count"] if successful else 0,
            "failed": failed["count"] if failed else 0,
        }
