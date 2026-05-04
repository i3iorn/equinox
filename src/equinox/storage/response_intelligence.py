"""Storage manager for Response Intelligence historical data.

Provides access to endpoint timing stats and schema snapshots
used by the percentile, anomaly, and drift analyzers.
"""

import hashlib
import logging
import math
from typing import Any, Dict, List, Optional

from equinox.core.exceptions import StorageError
from equinox.storage.database import Database
from equinox.storage.utils import safe_json_dumps, safe_json_loads

logger = logging.getLogger(__name__)


class ResponseIntelligenceManager:
    """Read / write Response Intelligence data in the database."""

    # ── Class-level limits ────────────────────────────────────────────

    # Maximum number of individual elapsed-ms samples kept per endpoint.
    _MAX_ELAPSED_SAMPLES: int = 100
    # Maximum serialized JSON size for the elapsed_values column.
    _MAX_ELAPSED_JSON_LEN: int = 50_000
    # Maximum serialized JSON size for a schema fingerprint.
    _MAX_SCHEMA_JSON_LEN: int = 200_000
    # Number of hex characters taken from the SHA-256 schema hash.
    _SCHEMA_HASH_LENGTH: int = 16
    # Key used when schema_json stores per-status snapshots.
    _SCHEMA_BY_STATUS_KEY: str = "_by_status"
    # Hard cap on rows returned by get_recent_history to avoid runaway queries.
    _MAX_HISTORY_LIMIT: int = 500

    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Endpoint stats ────────────────────────────────────────────────

    def get_endpoint_stats(
        self, url_pattern: str, method: str
    ) -> Optional[Dict[str, Any]]:
        """Return stored stats for an endpoint or ``None``."""
        row = self.db.fetchone(
            "SELECT * FROM endpoint_stats WHERE url_pattern = ? AND method = ?",
            (url_pattern, self._normalize_method(method)),
        )
        return dict(row) if row else None

    def update_endpoint_stats(
        self, url_pattern: str, method: str, elapsed_ms: float
    ) -> None:
        """Append a new timing sample and update aggregate stats.

        The read-modify-write cycle is wrapped in a transaction so concurrent
        callers cannot overwrite each other's elapsed_values updates.

        Raises:
            StorageError: If *elapsed_ms* is not a finite non-negative number.
        """
        if not math.isfinite(elapsed_ms) or elapsed_ms < 0:
            raise StorageError(
                f"elapsed_ms must be a non-negative finite number, got {elapsed_ms!r}"
            )
        method_upper = self._normalize_method(method)
        with self.db.transaction() as tx:
            existing = tx.fetchone(
                "SELECT call_count, total_elapsed, min_elapsed, max_elapsed, elapsed_values"
                " FROM endpoint_stats WHERE url_pattern = ? AND method = ?",
                (url_pattern, method_upper),
            )
            if existing is None:
                vals = safe_json_dumps(
                    [round(elapsed_ms, 2)], max_len=self._MAX_ELAPSED_JSON_LEN
                )
                tx.execute(
                    """INSERT INTO endpoint_stats
                       (url_pattern, method, call_count, total_elapsed,
                        min_elapsed, max_elapsed, elapsed_values, updated_at)
                       VALUES (?, ?, 1, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (url_pattern, method_upper, elapsed_ms, elapsed_ms, elapsed_ms, vals),
                )
            else:
                values = self._decode_elapsed_values(existing.get("elapsed_values"))
                values.append(round(elapsed_ms, 2))
                # Keep only the most recent N samples.
                values = values[-self._MAX_ELAPSED_SAMPLES:]

                new_count = (existing["call_count"] or 0) + 1
                new_total = (existing["total_elapsed"] or 0.0) + elapsed_ms
                # Guard against None (first row) and avoid the `or` falsy-zero
                # bug: a stored 0.0 must not be replaced by elapsed_ms.
                stored_min = existing["min_elapsed"]
                stored_max = existing["max_elapsed"]
                new_min = min(stored_min, elapsed_ms) if stored_min is not None else elapsed_ms
                new_max = max(stored_max, elapsed_ms) if stored_max is not None else elapsed_ms

                vals = safe_json_dumps(values, max_len=self._MAX_ELAPSED_JSON_LEN)
                tx.execute(
                    """UPDATE endpoint_stats
                       SET call_count = ?, total_elapsed = ?,
                           min_elapsed = ?, max_elapsed = ?,
                           elapsed_values = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE url_pattern = ? AND method = ?""",
                    (new_count, new_total, new_min, new_max, vals, url_pattern, method_upper),
                )

    # ── Schema snapshots ──────────────────────────────────────────────

    def get_schema(
        self,
        url_pattern: str,
        method: str,
        status_code: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return stored schema fingerprint for an endpoint/status.

        When *status_code* is provided, only the schema captured for that exact
        response code is returned. Legacy rows (single flat schema payload with
        no status metadata) return ``None`` in that mode to avoid cross-status
        comparisons.
        """
        row = self.db.fetchone(
            "SELECT schema_json FROM response_schemas "
            "WHERE url_pattern = ? AND method = ?",
            (url_pattern, self._normalize_method(method)),
        )
        if row is None:
            return None

        payload = safe_json_loads(row["schema_json"]) or None
        if not isinstance(payload, dict):
            return None

        by_status = payload.get(self._SCHEMA_BY_STATUS_KEY)
        if isinstance(by_status, dict):
            if status_code is not None:
                schema = by_status.get(str(int(status_code)))
                return schema if isinstance(schema, dict) else None
            default_schema = by_status.get("200")
            if isinstance(default_schema, dict):
                return default_schema
            for value in by_status.values():
                if isinstance(value, dict):
                    return value
            return None

        if status_code is not None:
            # Legacy payload: no reliable status binding available.
            return None
        return payload

    def save_schema(
        self,
        url_pattern: str,
        method: str,
        schema: Dict[str, str],
        status_code: Optional[int] = None,
    ) -> None:
        """Insert or replace the schema fingerprint for an endpoint.

        Uses a single-statement UPSERT to avoid the read-then-write race that
        two separate SELECT + INSERT/UPDATE calls would introduce.
        """
        payload: Dict[str, Any]
        if status_code is None:
            payload = schema
        else:
            payload = self._build_status_scoped_payload(
                url_pattern=url_pattern,
                method=method,
                status_code=int(status_code),
                schema=schema,
            )

        schema_json = safe_json_dumps(payload, max_len=self._MAX_SCHEMA_JSON_LEN)
        schema_hash = hashlib.sha256(schema_json.encode()).hexdigest()[: self._SCHEMA_HASH_LENGTH]

        self.db.execute(
            """INSERT INTO response_schemas
               (url_pattern, method, schema_hash, schema_json)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(url_pattern, method) DO UPDATE SET
                   schema_hash = excluded.schema_hash,
                   schema_json = excluded.schema_json,
                   captured_at = CURRENT_TIMESTAMP""",
            (url_pattern, self._normalize_method(method), schema_hash, schema_json),
        )

    def _build_status_scoped_payload(
        self,
        url_pattern: str,
        method: str,
        status_code: int,
        schema: Dict[str, str],
    ) -> Dict[str, Any]:
        """Return merged schema payload keyed by HTTP status code."""
        existing = self.db.fetchone(
            "SELECT schema_json FROM response_schemas WHERE url_pattern = ? AND method = ?",
            (url_pattern, self._normalize_method(method)),
        )

        by_status: Dict[str, Dict[str, str]] = {}
        if existing and existing.get("schema_json"):
            parsed = safe_json_loads(existing["schema_json"]) or {}
            if isinstance(parsed, dict):
                raw_map = parsed.get(self._SCHEMA_BY_STATUS_KEY)
                if isinstance(raw_map, dict):
                    for key, value in raw_map.items():
                        if isinstance(value, dict):
                            by_status[str(key)] = value

        by_status[str(status_code)] = schema
        return {self._SCHEMA_BY_STATUS_KEY: by_status}

    # ── Recent history for N+1 detection ──────────────────────────────

    def get_recent_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent history rows (url + method only).

        *limit* is clamped to ``[1, _MAX_HISTORY_LIMIT]`` to prevent runaway
        queries from misbehaving callers.
        """
        limit = max(1, min(limit, self._MAX_HISTORY_LIMIT))
        rows = self.db.fetchall(
            "SELECT method, url, elapsed, executed_at "
            "FROM history ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    # ── Private helpers ───────────────────────────────────────────────

    @staticmethod
    def _normalize_method(method: str) -> str:
        """Return *method* normalized to uppercase for consistent DB storage."""
        return method.upper()

    @staticmethod
    def _decode_elapsed_values(raw: Optional[str]) -> List[float]:
        """Parse the ``elapsed_values`` JSON column into a list of floats.

        Returns an empty list for null, empty, or corrupt values so callers
        never have to special-case these states themselves.
        """
        values = safe_json_loads(raw or "[]", default=[])
        return values if isinstance(values, list) else []
