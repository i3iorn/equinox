from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast
from typing import Protocol
from typing import TypeAlias

from PyQt6.QtCore import QPoint
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QListWidget
from PyQt6.QtWidgets import QListWidgetItem
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QSplitter
from PyQt6.QtWidgets import QTableWidget
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

from ..ui_common import configure_splitter_persistence
from .controller import VariablesTableController
from .dialog import GroupDialogs
from .group_service import GroupService
from .protocols import GroupManagerProtocol
from .protocols import SettingsProtocol

logger = logging.getLogger(__name__)


class _SignalLike(Protocol):
    def emit(self) -> None: ...


ContextActionSpec: TypeAlias = tuple[str, str, Callable[[], None], bool]


class _GroupsMixin:
    """
    Slim UI mixin for variable groups and variables.

    All business logic is delegated to:
        - GroupService
        - GroupDialogs
        - VariablesTableController
    """

    # Injected by parent widget
    _mgr: GroupManagerProtocol
    _settings: SettingsProtocol
    variables_changed: _SignalLike
    _ordered_context_actions: Callable[[str, list[ContextActionSpec]], list[ContextActionSpec]]
    _run_context_action: Callable[[str, str, Callable[[], None]], None]

    # Runtime state
    current_group_id: int | None

    # UI elements
    groups_list: QListWidget
    variables_table: QTableWidget
    new_group_btn: QPushButton
    delete_group_btn: QPushButton
    add_var_btn: QPushButton
    edit_var_btn: QPushButton
    remove_var_btn: QPushButton

    # Services
    group_service: GroupService
    dialogs: GroupDialogs
    table_controller: VariablesTableController

    # ----------------------------------------------------------------------
    # Initialization
    # ----------------------------------------------------------------------

    def _init_group_services(self) -> None:
        """Call this once in the parent widget's __init__."""
        parent = cast(QWidget, self)
        self.group_service = GroupService(self._mgr, logger)
        self.dialogs = GroupDialogs(parent)
        self.table_controller = VariablesTableController(self.variables_table)

    def _build_interp_context(self) -> dict[str, str]:
        """Implemented by the host panel to build interpolation preview context."""
        raise NotImplementedError

    # ----------------------------------------------------------------------
    # UI construction
    # ----------------------------------------------------------------------

    def _build_groups_section(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        splitter.addWidget(self._build_groups_panel())
        splitter.addWidget(self._build_variables_panel())

        self._configure_splitter(splitter)
        return splitter

    def _build_groups_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        layout.addWidget(QLabel("<b>Groups</b>"))
        layout.addLayout(self._build_groups_toolbar())

        self.groups_list = QListWidget()
        self.groups_list.itemSelectionChanged.connect(self._on_group_selected)
        self.groups_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.groups_list.customContextMenuRequested.connect(self._show_group_context_menu)
        layout.addWidget(self.groups_list)

        return panel

    def _build_variables_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        layout.addWidget(QLabel("<b>Variables</b>"))
        layout.addLayout(self._build_variables_toolbar())

        self.variables_table = QTableWidget()
        self.variables_table.setColumnCount(3)
        self.variables_table.setHorizontalHeaderLabels(["Key", "Value", "Description"])
        self.variables_table.itemSelectionChanged.connect(self._on_variable_selected)
        self.variables_table.itemDoubleClicked.connect(self.edit_variable)

        layout.addWidget(self.variables_table)
        return panel

    def _build_groups_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        self.new_group_btn = QPushButton("New Group")
        self.new_group_btn.clicked.connect(self.create_group)

        self.delete_group_btn = QPushButton("Delete Group")
        self.delete_group_btn.setEnabled(False)
        self.delete_group_btn.clicked.connect(self.delete_group)

        bar.addWidget(self.new_group_btn)
        bar.addWidget(self.delete_group_btn)
        bar.addStretch()
        return bar

    def _build_variables_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        self.add_var_btn = QPushButton("Add Variable")
        self.add_var_btn.setEnabled(False)
        self.add_var_btn.clicked.connect(self.add_variable)

        self.edit_var_btn = QPushButton("Edit")
        self.edit_var_btn.setEnabled(False)
        self.edit_var_btn.clicked.connect(self.edit_variable)

        self.remove_var_btn = QPushButton("Remove")
        self.remove_var_btn.setEnabled(False)
        self.remove_var_btn.clicked.connect(self.remove_variable)

        bar.addWidget(self.add_var_btn)
        bar.addWidget(self.edit_var_btn)
        bar.addWidget(self.remove_var_btn)
        bar.addStretch()
        return bar

    def _configure_splitter(self, splitter: QSplitter) -> None:
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        configure_splitter_persistence(
            splitter,
            settings_key="splitter/variables",
            default_sizes=[250, 550],
            settings=self._settings,
        )

    # ----------------------------------------------------------------------
    # Group refresh
    # ----------------------------------------------------------------------

    def refresh_groups(self) -> None:
        self.groups_list.blockSignals(True)
        self.groups_list.setUpdatesEnabled(False)

        try:
            self.groups_list.clear()
            self.current_group_id = None

            for group in self.group_service.list_groups():
                item = QListWidgetItem(group["name"])
                item.setData(Qt.ItemDataRole.UserRole, group["id"])
                if group.get("description"):
                    item.setToolTip(group["description"])
                self.groups_list.addItem(item)

        finally:
            self.groups_list.setUpdatesEnabled(True)
            self.groups_list.blockSignals(False)

    # ----------------------------------------------------------------------
    # Variable refresh
    # ----------------------------------------------------------------------

    def refresh_variables(self) -> None:
        if not self.current_group_id:
            self.variables_table.setRowCount(0)
            return

        try:
            vars_ = self.group_service.list_variables(self.current_group_id)
        except Exception as exc:
            logger.error("Failed to load variables: %s", exc, exc_info=True)
            return

        interp = self._build_interp_context()
        self.table_controller.load(vars_, interp)

    # ----------------------------------------------------------------------
    # Group slots
    # ----------------------------------------------------------------------

    def _on_group_selected(self) -> None:
        selected = self.groups_list.selectedItems()
        if not selected:
            self.current_group_id = None
            self.delete_group_btn.setEnabled(False)
            self.add_var_btn.setEnabled(False)
            self.variables_table.setRowCount(0)
            return

        item = selected[0]
        self.current_group_id = cast(int, item.data(Qt.ItemDataRole.UserRole))
        self.delete_group_btn.setEnabled(True)
        self.add_var_btn.setEnabled(True)
        self.refresh_variables()

    def create_group(self) -> None:
        name, ok = self.dialogs.ask_group_name()
        if not ok or not name:
            return

        desc, _ = self.dialogs.ask_group_description()

        try:
            self.group_service.create_group(name, desc or "")
            self.refresh_groups()
            self.variables_changed.emit()
            self.dialogs.show_success(f"Variable group '{name}' created")
        except Exception as exc:
            logger.error("Failed to create group: %s", exc, exc_info=True)
            self.dialogs.show_error(f"Failed to create group: {exc}")

    def delete_group(self) -> None:
        if not self.current_group_id:
            return

        selected = self.groups_list.selectedItems()
        if not selected:
            return

        name = selected[0].text()
        if not self.dialogs.confirm_delete_group(name):
            return

        try:
            self.group_service.delete_group(self.current_group_id)
            self.refresh_groups()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to delete group: %s", exc, exc_info=True)
            self.dialogs.show_error(f"Failed to delete group: {exc}")

    def _show_group_context_menu(self, pos: QPoint) -> None:
        item = self.groups_list.itemAt(pos)
        if not item:
            return

        menu = QMenu()
        actions: list[ContextActionSpec] = [
            ("rename", "Rename", lambda: self._rename_group(item), False),
            ("delete", "Delete", self.delete_group, True),
        ]

        ordered = self._ordered_context_actions("variables_group", actions)

        destructive_added = False
        for action_id, label, callback, destructive in ordered:
            if destructive and not destructive_added:
                menu.addSeparator()
                destructive_added = True

            menu.addAction(
                label,
                lambda aid=action_id, cb=callback: self._run_context_action(
                    "variables_group", aid, cb,
                ),
            )

        viewport = self.groups_list.viewport()
        if viewport is None:
            return
        menu.exec(viewport.mapToGlobal(pos))

    def _rename_group(self, item: QListWidgetItem) -> None:
        old_name = item.text()
        new_name, ok = self.dialogs.ask_rename_group(old_name)
        if not ok or not new_name:
            return

        group_id = cast(int, item.data(Qt.ItemDataRole.UserRole))

        try:
            self.group_service.rename_group(group_id, new_name)
            self.refresh_groups()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to rename group: %s", exc, exc_info=True)
            self.dialogs.show_error(f"Failed to rename group: {exc}")

    # ----------------------------------------------------------------------
    # Variable slots
    # ----------------------------------------------------------------------

    def _on_variable_selected(self) -> None:
        has = bool(self.variables_table.selectedItems())
        self.edit_var_btn.setEnabled(has)
        self.remove_var_btn.setEnabled(has)

    def add_variable(self) -> None:
        if not self.current_group_id:
            return

        result = self.dialogs.new_variable()
        if not result:
            return

        key, value, desc = result

        try:
            self.group_service.add_variable(self.current_group_id, key, value, desc)
            self.refresh_variables()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to add variable: %s", exc, exc_info=True)
            self.dialogs.show_error(f"Failed to add variable: {exc}")

    def edit_variable(self) -> None:
        if not self.current_group_id:
            return

        row = self.variables_table.currentRow()
        if row < 0:
            return

        key_item = self.variables_table.item(row, 0)
        value_item = self.variables_table.item(row, 1)
        desc_item = self.variables_table.item(row, 2)
        if key_item is None or value_item is None or desc_item is None:
            return

        key = key_item.text()
        value = value_item.text()
        desc = desc_item.text()

        result = self.dialogs.edit_variable(key, value, desc)
        if not result:
            return

        new_key, new_value, new_desc = result

        try:
            self.group_service.update_variable(
                self.current_group_id,
                key,
                new_key,
                new_value,
                new_desc,
            )
            self.refresh_variables()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to update variable: %s", exc, exc_info=True)
            self.dialogs.show_error(f"Failed to update variable: {exc}")

    def remove_variable(self) -> None:
        if not self.current_group_id:
            return

        row = self.variables_table.currentRow()
        if row < 0:
            return

        key_item = self.variables_table.item(row, 0)
        if key_item is None:
            return

        key = key_item.text()

        if not self.dialogs.confirm_delete_group(key):
            return

        try:
            self.group_service.remove_variable(self.current_group_id, key)
            self.refresh_variables()
            self.variables_changed.emit()
        except Exception as exc:
            logger.error("Failed to remove variable: %s", exc, exc_info=True)
            self.dialogs.show_error(f"Failed to remove variable: {exc}")
