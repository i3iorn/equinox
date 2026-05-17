"""Environment switching and status bar mixin for MainWindow."""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMenu, QToolButton

from equinox.storage import EnvironmentManager

logger = logging.getLogger(__name__)

_STATUS_TIMEOUT_MS = 10_000


class _EnvironmentMixin:
    """Status bar with active-environment display and quick-switch menu."""

    def _env_usage_count(self, env_id: int) -> int:
        tracker = getattr(self, "_ui_usage_tracker", None)
        if tracker is None:
            return 0
        try:
            return tracker.get_count(
                category="environment",
                context="quick_switch",
                element_id=f"env.{int(env_id)}",
            )
        except Exception:
            logger.debug("Could not read environment usage for id=%s", env_id, exc_info=True)
            return 0

    def _rank_environments(self, envs: list, active_id: object) -> list:
        """Order envs by UX-friendly rules: active first, then usage, then name."""
        ranked = []
        for env in envs:
            env_id = int(env.get("id") or 0)
            usage = self._env_usage_count(env_id)
            name = str(env.get("name") or "")
            is_active = env_id == active_id
            ranked.append((not is_active, -usage, name.casefold(), env))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        return [item[3] for item in ranked]

    def _create_status_bar(self) -> None:
        from PyQt6.QtWidgets import QStatusBar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self._env_btn = QToolButton()
        self._env_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._env_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._env_btn.setToolTip("Active environment — click to switch, ▸ to manage")

        self._env_menu = QMenu(self._env_btn)
        self._env_btn.setMenu(self._env_menu)
        self._env_btn.clicked.connect(self._show_env_menu)
        self.status_bar.addPermanentWidget(self._env_btn)

        self._refresh_env_label()
        self.status_bar.showMessage("Ready")

    def _show_env_menu(self) -> None:
        """Build and show the environment quick-switch menu."""
        self._env_menu.clear()
        try:
            mgr = EnvironmentManager(self.db)
            envs = mgr.list_environments()
            active = mgr.get_active_environment()
            active_id = active["id"] if active else None
            envs = self._rank_environments(envs, active_id)

            for env in envs:
                action = self._env_menu.addAction(env["name"])
                action.setCheckable(True)
                action.setChecked(env["id"] == active_id)
                action.triggered.connect(
                    lambda checked, eid=env["id"]: self._switch_environment(eid)
                )
            if envs:
                self._env_menu.addSeparator()

            manage = self._env_menu.addAction("Manage Environments…")
            manage.triggered.connect(self._manage_environments)
        except Exception:
            logger.debug("Failed to build env menu", exc_info=True)
            manage = self._env_menu.addAction("Manage Environments…")
            manage.triggered.connect(self._manage_environments)

        self._env_menu.popup(
            self._env_btn.mapToGlobal(self._env_btn.rect().topLeft())
        )

    def _switch_environment(self, env_id: int) -> None:
        """Activate the given environment and refresh the label."""
        try:
            EnvironmentManager(self.db).set_active_environment(env_id)
            tracker = getattr(self, "_ui_usage_tracker", None)
            if tracker is not None:
                tracker.record(
                    f"env.{int(env_id)}",
                    category="environment",
                    context="quick_switch",
                )
            self._refresh_env_label()
            self.request_panel.refresh_inherited_auth()
        except Exception:
            logger.debug("Failed to switch environment to %d", env_id, exc_info=True)

    def _refresh_env_label(self) -> None:
        try:
            env = EnvironmentManager(self.db).get_active_environment()
            if env:
                self._env_btn.setText(f"🌍  {env['name']}")
            else:
                self._env_btn.setText("No environment")
        except Exception:
            logger.debug("Failed to refresh env label", exc_info=True)

    def _manage_environments(self) -> None:
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog
        EnvironmentDialog(self.db, self).exec()
        if self.variables_panel:
            self.variables_panel.refresh()
        self._refresh_env_label()

    def _manage_oauth_clients(self) -> None:
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog
        SavedCredentialsDialog(self.db, self).exec()

    def _manage_secret_managers(self) -> None:
        from equinox.gui.dialogs.secret_manager_settings_dialog import SecretManagerSettingsDialog
        SecretManagerSettingsDialog(parent=self).exec()

