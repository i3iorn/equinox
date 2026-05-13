"""History loading and response reconstruction mixin for MainWindow."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import QMessageBox

from equinox.core.request import Request, Response
from equinox.storage import HistoryManager

logger = logging.getLogger(__name__)


class _HistoryMixin:
    """Methods for loading and replaying requests from history."""

    # ── Static coercion helpers ───────────────────────────────────────────────

    @staticmethod
    def _coerce_to_dict(value: object, field_name: str) -> dict:
        """Return *value* as a plain dict, logging and returning ``{}`` on failure."""
        if isinstance(value, dict):
            return value
        try:
            return dict(value)  # type: ignore[arg-type]
        except Exception:
            logger.debug(
                "Could not coerce %s to dict, defaulting to {}", field_name, exc_info=True
            )
            return {}

    @staticmethod
    def _coerce_body_to_bytes(raw: object) -> bytes:
        """Decode *raw* (str, bytes, or other) to ``bytes`` for Response construction."""
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, str):
            return raw.encode("utf-8")
        try:
            return str(raw).encode("utf-8")
        except Exception:
            return b""

    @staticmethod
    def _parse_timestamp(value: object) -> Optional[datetime]:
        """Parse an ISO-8601 string into a ``datetime``, or ``None`` on any failure."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            logger.debug("Could not parse timestamp: %s", value)
            return None

    # ── History DB helpers ────────────────────────────────────────────────────

    @staticmethod
    def _request_from_history(entry: dict) -> Request:
        """Build a Request from a history DB row."""
        headers = _HistoryMixin._coerce_to_dict(
            entry.get("request_headers") or {}, "request_headers"
        )
        params = _HistoryMixin._coerce_to_dict(
            entry.get("request_params") or {}, "request_params"
        )

        body = entry.get("request_body")
        if isinstance(body, bytes):
            try:
                body = body.decode("utf-8")
            except Exception:
                body = body.decode("utf-8", errors="replace")
        elif body is not None and not isinstance(body, str):
            body = str(body)

        return Request(
            method=entry.get("method", "GET"),
            url=entry.get("url", ""),
            headers=headers,
            params=params,
            body=body,
        )

    def _fetch_history_entry(self, history_id: int) -> Optional[dict]:
        """Fetch a history entry by ID, or None."""
        return HistoryManager(self.db).get_history(history_id)

    def _fetch_and_load_history(
        self, history_id: int
    ) -> "Optional[tuple[dict, Request]]":
        """Autosave, fetch, build, and load a history entry into the request panel.

        Returns ``(entry, request)`` on success, ``None`` when the entry is absent.
        """
        self.request_panel.autosave_current()
        entry = self._fetch_history_entry(history_id)
        if not entry:
            logger.debug("_fetch_and_load_history: no entry for id=%s", history_id)
            return None
        request = self._request_from_history(entry)
        try:
            self.request_panel.load_request(request)
        except Exception:
            logger.error(
                "Failed to load request from history id=%s", history_id, exc_info=True
            )
        return entry, request

    def _build_response_from_history(
        self, entry: dict, request: Request, history_id: int
    ) -> Optional[Response]:
        """Reconstruct a ``Response`` from a history DB row, or ``None``."""
        if entry.get("status_code") is None:
            return None

        body_bytes = self._coerce_body_to_bytes(entry.get("response_body") or "")
        timestamp = self._parse_timestamp(entry.get("executed_at")) or datetime.now()
        headers = self._coerce_to_dict(
            entry.get("response_headers") or {}, "response_headers"
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
                "Failed to construct Response for history id=%s", history_id, exc_info=True
            )
            return None

        logger.debug(
            "Built history response id=%s (status=%s size=%s)",
            history_id, entry.get("status_code"), len(body_bytes),
        )
        return response

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _load_history_entry(self, history_id: int) -> None:
        """Load and display a history entry in the request/response panels."""
        try:
            result = self._fetch_and_load_history(history_id)
            if result is None:
                return
            entry, request = result
            response = self._build_response_from_history(entry, request, history_id)
            if response is not None:
                self.response_panel.display_response(response)
                self._run_intelligence_analysis(response)
            else:
                self.response_panel.intelligence_panel.clear()
                self.response_panel.set_intelligence_badge(0)
        except Exception:
            logger.error(
                "Unhandled error loading history entry id=%s", history_id, exc_info=True
            )
            try:
                QMessageBox.critical(
                    self, "Error",
                    f"Failed to load history entry {history_id}. See log for details.",
                )
            except Exception:
                logger.debug("Also failed to show error dialog for history load", exc_info=True)

    def _replay_history_entry(self, history_id: int) -> None:
        """Re-run a history entry exactly as originally sent."""
        if self._fetch_and_load_history(history_id) is None:
            return
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self.request_panel.send)

