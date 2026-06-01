"""Dirty-state signal wiring mixin for RequestPanel."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from typing import TYPE_CHECKING

DIRTY_SIGNAL_BINDINGS: list[tuple[str, str, str]] = [
    ("url_input.textChanged", "_mark_dirty", "url_input.textChanged"),
    ("method_combo.currentIndexChanged", "_mark_dirty", "method_combo.currentIndexChanged"),
    ("body_text.textChanged", "_mark_dirty", "body_text.textChanged"),
    ("body_text.textChanged", "_update_tab_labels", "body_text.textChanged->update_tab_labels"),
    ("body_type_combo.currentIndexChanged", "_mark_dirty", "body_type_combo.currentIndexChanged"),
    ("headers_table.itemChanged", "_mark_dirty", "headers_table.itemChanged"),
    (
        "headers_table.itemChanged",
        "_update_tab_labels",
        "headers_table.itemChanged->update_tab_labels",
    ),
    ("params_table.itemChanged", "_mark_dirty", "params_table.itemChanged"),
    (
        "params_table.itemChanged",
        "_update_tab_labels",
        "params_table.itemChanged->update_tab_labels",
    ),
    (
        "params_table.itemChanged",
        "_update_url_suffix",
        "params_table.itemChanged->update_url_suffix",
    ),
    ("url_input.textChanged", "_update_url_suffix", "url_input.textChanged->update_url_suffix"),
    ("path_params_table.paramsChanged", "_mark_dirty", "path_params_table.paramsChanged"),
    ("_multipart_table.itemChanged", "_mark_dirty", "_multipart_table.itemChanged"),
    (
        "_multipart_table.itemChanged",
        "_update_tab_labels",
        "_multipart_table.itemChanged->update_tab_labels",
    ),
    ("timeout_spin.valueChanged", "_mark_dirty", "timeout_spin.valueChanged"),
    ("verify_ssl_check.stateChanged", "_mark_dirty", "verify_ssl_check.stateChanged"),
    (
        "verify_ssl_check.stateChanged",
        "_refresh_auth_display_from_state",
        "verify_ssl_check.stateChanged->auth_display",
    ),
    ("follow_redirects_check.stateChanged", "_mark_dirty", "follow_redirects_check.stateChanged"),
    (
        "url_input.textChanged",
        "_refresh_auth_display_from_state",
        "url_input.textChanged->auth_display",
    ),
    ("notes_editor.textChanged", "_mark_dirty", "notes_editor.textChanged"),
    ("_gql_query.textChanged", "_mark_dirty", "_gql_query.textChanged"),
    ("_gql_vars.textChanged", "_mark_dirty", "_gql_vars.textChanged"),
]


class DirtyTrackingMixin:
    """Mixin providing dirty-state signal wiring for RequestPanel."""

    if TYPE_CHECKING:

        def _update_auth_display(self, auth: Any = None) -> None: ...

    def _dirty_logger(self) -> logging.Logger:
        candidate = getattr(self, "logger", None)
        if isinstance(candidate, logging.Logger):
            return candidate
        return logging.getLogger(__name__)

    def _setup_dirty_tracking(self) -> None:
        """Compatibility wrapper used by ``RequestPanel``."""
        self.setup_dirty_tracking()

    def setup_dirty_tracking(self) -> None:
        """Connect editor signals that should mark the request as dirty."""
        connected_count = 0
        for get_signal, slot, name in self._dirty_signal_bindings():
            if self._safe_connect(get_signal, slot, name):
                connected_count += 1
        self._dirty_logger().debug("Dirty tracking: %d signal(s) connected", connected_count)

    def _refresh_auth_display_from_state(self, *_args: Any) -> None:
        """Refresh auth display using current own/inherited auth state."""
        try:
            self._update_auth_display(getattr(self, "_auth", None))
        except Exception:
            self._dirty_logger().debug(
                "Failed to refresh auth display during dirty tracking",
                exc_info=True,
            )

    def _safe_connect(
        self,
        get_signal: Callable[[], Any],
        slot: Callable[..., Any],
        name: str,
    ) -> bool:
        """Safely retrieve and connect a Qt signal."""
        logger = self._dirty_logger()
        try:
            signal = get_signal()
        except (AttributeError, RuntimeError) as exc:
            logger.debug(
                "Signal retrieval skipped (C++ object missing): %s - %s",
                name,
                type(exc).__name__,
            )
            return False
        except Exception:
            logger.warning("Unexpected error retrieving signal: %s", name, exc_info=True)
            return False

        try:
            signal.connect(slot)
            return True
        except RuntimeError:
            logger.debug("Failed to connect signal after retrieval: %s", name)
            return False

    def _dirty_signal_bindings(
        self,
    ) -> list[tuple[Callable[[], Any], Callable[..., Any], str]]:
        return [
            (self._resolve_signal(path), getattr(self, slot), name)
            for path, slot, name in DIRTY_SIGNAL_BINDINGS
        ]

    def _resolve_signal(self, dotted: str) -> Callable[[], Any]:
        def getter() -> Any:
            obj_path, signal_name = dotted.rsplit(".", 1)
            obj = self
            for part in obj_path.split("."):
                obj = getattr(obj, part)
            return getattr(obj, signal_name)

        return getter
