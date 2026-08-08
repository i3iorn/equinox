"""History facade for history-panel and window replay flows.

This module centralizes history reads/writes and history-row reconstruction so
GUI modules do not construct ``HistoryManager`` directly and do not duplicate
entry-to-model mapping logic.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from equinox.core.request import Request
from equinox.core.request import Response
from equinox.storage import Database
from equinox.storage import HistoryManager

logger = logging.getLogger(__name__)


class HistoryFacade:
    """Application boundary for GUI history operations."""

    def __init__(
        self,
        db: Database,
        history_manager: HistoryManager | None = None,
    ) -> None:
        self._history_manager = history_manager or HistoryManager(db)

    # ── History manager wrappers ───────────────────────────────────────

    def get_history(self, history_id: int) -> dict[str, str | int | float | bool | object] | None:
        return self._history_manager.get_history(history_id)

    def search_history(self, **filters: Any) -> list[dict[str, Any]]:
        return list(self._history_manager.search_history(**filters))

    def get_stats(self) -> dict[str, str | int | float | bool | object]:
        return self._history_manager.get_stats()

    def delete_history(self, history_id: int) -> None:
        self._history_manager.delete_history(history_id)

    def clear_history(self, days: int | None = None) -> None:
        self._history_manager.clear_history(days=days)

    # ── History row reconstruction ─────────────────────────────────────

    @staticmethod
    def _coerce_to_dict(value: object, field_name: str) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, Mapping):
            try:
                return {str(k): v for k, v in value.items()}
            except Exception:
                logger.exception(
                    "Could not coerce %s mapping-like value to dict",
                    field_name,
                    exc_info=True,
                )
                raise
        logger.debug("Could not coerce %s to dict, defaulting to {}", field_name)
        return {}

    @staticmethod
    def _coerce_body_to_bytes(raw: object) -> bytes:
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, str):
            return raw.encode("utf-8")
        return str(raw).encode("utf-8")

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            logger.debug("Could not parse timestamp: %s", value)
            return None

    @staticmethod
    def request_from_entry(entry: dict[str, Any]) -> Request:
        """Build a Request instance from a history row dict."""
        headers = HistoryFacade._coerce_to_dict(
            entry.get("request_headers") or {},
            "request_headers",
        )
        params = HistoryFacade._coerce_to_dict(entry.get("request_params") or {}, "request_params")

        body = entry.get("request_body")
        if isinstance(body, bytes):
            raw_body = body
            try:
                body = raw_body.decode("utf-8")
            except Exception:
                body = raw_body.decode("utf-8", errors="replace")
        elif body is not None and not isinstance(body, str):
            body = str(body)

        return Request(
            method=entry.get("method", "GET"),
            url=entry.get("url", ""),
            headers=headers,
            params=params,
            body=body,
        )

    @staticmethod
    def response_from_entry(
        entry: dict[str, Any],
        request: Request,
        history_id: int | None = None,
    ) -> Response | None:
        """Build a Response from a history row dict, or None when absent."""
        if entry.get("status_code") is None:
            return None

        body_bytes = HistoryFacade._coerce_body_to_bytes(entry.get("response_body") or "")
        timestamp = HistoryFacade._parse_timestamp(entry.get("executed_at")) or datetime.now()
        headers = HistoryFacade._coerce_to_dict(
            entry.get("response_headers") or {},
            "response_headers",
        )

        try:
            response = Response(
                status_code=int(entry.get("status_code") or 0),
                reason=entry.get("reason") or "",
                headers=headers,
                body=body_bytes,
                elapsed=float(entry.get("elapsed") or 0.0),
                request=request,
                timestamp=timestamp,
            )
        except Exception:
            logger.error(
                "Failed to construct Response for history id=%s",
                history_id if history_id is not None else "unknown",
                exc_info=True,
            )
            raise

        logger.debug(
            "Built history response id=%s (status=%s size=%s)",
            history_id if history_id is not None else "unknown",
            entry.get("status_code"),
            len(body_bytes),
        )
        return response
