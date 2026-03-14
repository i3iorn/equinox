"""Storage manager for Response Intelligence historical data.

Provides access to endpoint timing stats and schema snapshots
used by the percentile, anomaly, and drift analyzers.
"""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from equinox.storage.database import Database

logger = logging.getLogger(__name__)

# Keep at most this many individual elapsed values per endpoint
_MAX_ELAPSED_SAMPLES = 100


class ResponseIntelligenceManager:
    """Read / write Response Intelligence data in the database."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Endpoint stats ────────────────────────────────────────────────

    def get_endpoint_stats(
        self, url_pattern: str, method: str
    ) -> Optional[Dict[str, Any]]:
        """Return stored stats for an endpoint or ``None``."""
        rows = self.db.fetchall(
            "SELECT * FROM endpoint_stats WHERE url_pattern = ? AND method = ?",
            (url_pattern, method.upper()),
        )
        if rows:
            return dict(rows[0])
        return None

    def update_endpoint_stats(
        self, url_pattern: str, method: str, elapsed_ms: float
    ) -> None:
        """Append a new timing sample and update aggregate stats."""
        existing = self.get_endpoint_stats(url_pattern, method)
        if existing is None:
            self.db.execute(
                """INSERT INTO endpoint_stats
                   (url_pattern, method, call_count, total_elapsed,
                    min_elapsed, max_elapsed, elapsed_values, updated_at)
                   VALUES (?, ?, 1, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    url_pattern,
                    method.upper(),
                    elapsed_ms,
                    elapsed_ms,
                    elapsed_ms,
                    json.dumps([elapsed_ms]),
                ),
            )
        else:
            try:
                values = json.loads(existing.get("elapsed_values") or "[]")
            except Exception:
                values = []
            values.append(round(elapsed_ms, 2))
            # Keep only most recent N samples
            values = values[-_MAX_ELAPSED_SAMPLES:]

            new_count = (existing.get("call_count") or 0) + 1
            new_total = (existing.get("total_elapsed") or 0) + elapsed_ms
            new_min = min(existing.get("min_elapsed") or elapsed_ms, elapsed_ms)
            new_max = max(existing.get("max_elapsed") or elapsed_ms, elapsed_ms)

            self.db.execute(
                """UPDATE endpoint_stats
                   SET call_count = ?, total_elapsed = ?,
                       min_elapsed = ?, max_elapsed = ?,
                       elapsed_values = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE url_pattern = ? AND method = ?""",
                (
                    new_count,
                    new_total,
                    new_min,
                    new_max,
                    json.dumps(values),
                    url_pattern,
                    method.upper(),
                ),
            )

    # ── Schema snapshots ──────────────────────────────────────────────

    def get_schema(
        self, url_pattern: str, method: str
    ) -> Optional[Dict[str, Any]]:
        """Return the stored schema fingerprint dict or ``None``."""
        rows = self.db.fetchall(
            "SELECT schema_json FROM response_schemas "
            "WHERE url_pattern = ? AND method = ?",
            (url_pattern, method.upper()),
        )
        if rows:
            try:
                return json.loads(rows[0]["schema_json"])
            except Exception:
                return None
        return None

    def save_schema(
        self, url_pattern: str, method: str, schema: Dict[str, str]
    ) -> None:
        """Insert or replace the schema fingerprint for an endpoint."""
        schema_json = json.dumps(schema, sort_keys=True)
        schema_hash = hashlib.sha256(schema_json.encode()).hexdigest()[:16]

        existing = self.db.fetchall(
            "SELECT id FROM response_schemas WHERE url_pattern = ? AND method = ?",
            (url_pattern, method.upper()),
        )
        if existing:
            self.db.execute(
                """UPDATE response_schemas
                   SET schema_hash = ?, schema_json = ?, captured_at = CURRENT_TIMESTAMP
                   WHERE url_pattern = ? AND method = ?""",
                (schema_hash, schema_json, url_pattern, method.upper()),
            )
        else:
            self.db.execute(
                """INSERT INTO response_schemas
                   (url_pattern, method, schema_hash, schema_json)
                   VALUES (?, ?, ?, ?)""",
                (url_pattern, method.upper(), schema_hash, schema_json),
            )

    # ── Recent history for N+1 detection ──────────────────────────────

    def get_recent_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent history rows (url + method only)."""
        rows = self.db.fetchall(
            "SELECT method, url, elapsed, executed_at "
            "FROM history ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

