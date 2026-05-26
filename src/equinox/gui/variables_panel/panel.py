"""VariablesPanel — main panel composing all variable management sections."""

from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from equinox.core.interpolation import collect_interpolation_variables_detailed
from equinox.storage import (
    Database,
    EnvironmentManager,
    GlobalVariablesManager,
    VariableGroupManager,
)

from ..ui_common import create_panel_layout, get_gui_settings
from ._context_menu_mixin import _ContextMenuMixin
from ._global_vars_mixin import _GlobalVarsMixin
from ._groups_mixin import _GroupsMixin
from ._session_vars_mixin import _SessionVarsMixin

logger = logging.getLogger(__name__)


class VariablesPanel(
    _ContextMenuMixin,
    _GlobalVarsMixin,
    _SessionVarsMixin,
    _GroupsMixin,
    QWidget,
):
    """Panel for managing variable groups and viewing captured session variables."""

    variables_changed = pyqtSignal()
    clear_session_requested = pyqtSignal()

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        # Lightweight DB wrappers — safe to cache across calls.
        self._mgr = VariableGroupManager(db)
        self._global_mgr = GlobalVariablesManager(db)
        self._env_mgr = EnvironmentManager(db)
        self.current_group_id: int | None = None
        self._settings = get_gui_settings()
        self._global_var_count = 0
        self._session_var_count = 0
        self._init_ui()
        self.refresh_global_vars()
        self.refresh_groups()

    def _init_ui(self) -> None:
        layout = create_panel_layout(self)
        layout.addWidget(self._build_global_vars_section())
        layout.addWidget(self._build_session_vars_section())
        layout.addWidget(self._build_groups_section())
        self._resize_global_table_to_content()
        self._resize_session_table_to_content()

    # ── Public refresh ────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload groups and current variables."""
        self.refresh_global_vars()
        self.refresh_groups()
        if self.current_group_id:
            self.refresh_variables()

    # ── Interpolation context ─────────────────────────────────────────────────

    def _build_interp_context(self) -> dict[str, str]:
        """Build the variable resolution map used for tooltip previews.

        Mirrors the request-time resolution order and silently degrades on
        failure so a broken source does not prevent the rest from contributing.
        """
        try:
            rp = getattr(self.window(), "request_panel", None)
            session_vars = rp.get_session_vars() if rp is not None else {}
            collection_id = getattr(
                getattr(rp, "current_request", None), "collection_id", None
            )
            interp_vars, _sources = collect_interpolation_variables_detailed(
                self.db,
                collection_id=collection_id,
                session_vars=session_vars,
            )
            if rp is not None:
                path_table = getattr(rp, "path_params_table", None)
                if path_table is not None:
                    interp_vars.update(path_table.get_all_data())
            return interp_vars
        except Exception as exc:
            logger.debug("Tooltip: failed to build interpolation context: %s", exc)
            return {}


