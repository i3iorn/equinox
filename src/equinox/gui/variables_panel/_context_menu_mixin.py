"""Context-menu usage-tracking helpers for VariablesPanel."""

# Ignore [attr-defined] errors as this will be mixed into a QWidget subclass that has the necessary attributes.
# mypy: disable-error-code="attr-defined"
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class _ContextMenuMixin:
    """Mixin that provides usage-ranked context menu infrastructure.

    Depends on the host widget exposing ``self.window()`` that may carry a
    ``_ui_usage_tracker`` attribute.
    """

    def _context_action_usage_count(self, context: str, action_id: str) -> int:
        """Return how many times *action_id* has been used in *context*."""
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return 0
        try:
            return int(
                tracker.get_count(
                    category="context_menu",
                    context=context,
                    element_id=f"action.{action_id}",
                ),
            )
        except Exception:
            logger.exception(
                "Failed to get context action usage for %s/%s",
                context,
                action_id,
                exc_info=True,
            )
            return 0

    def _record_context_action_usage(self, context: str, action_id: str) -> None:
        """Increment the usage counter for *action_id* in *context*."""
        tracker = getattr(self.window(), "_ui_usage_tracker", None)
        if tracker is None:
            return
        try:
            tracker.record(
                f"action.{action_id}",
                category="context_menu",
                context=context,
            )
        except Exception:
            logger.exception(
                "Failed to record context action usage for %s/%s",
                context,
                action_id,
                exc_info=True,
            )

    def _run_context_action(self, context: str, action_id: str, callback: Any) -> None:
        """Record usage and invoke *callback*."""
        self._record_context_action_usage(context, action_id)
        callback()

    def _ordered_context_actions(
        self,
        context: str,
        action_specs: list[tuple[str, str, Any, bool]],
    ) -> list[tuple[str, str, Any, bool]]:
        """Return action specs sorted by usage frequency.

        Non-destructive actions are sorted most-used first.
        Destructive actions are always placed last (after a separator).
        """
        safe: list[tuple[int, int, tuple[str, str, Any, bool]]] = []
        destructive: list[tuple[int, tuple[str, str, Any, bool]]] = []

        for idx, spec in enumerate(action_specs):
            action_id, _label, _callback, is_destructive = spec
            if is_destructive:
                destructive.append((idx, spec))
                continue
            count = self._context_action_usage_count(context, action_id)
            safe.append((-count, idx, spec))

        safe.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in safe] + [row[1] for row in destructive]
