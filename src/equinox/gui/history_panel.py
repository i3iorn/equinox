"""History panel"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import Any

from equinox.application.history import HistoryFacade
from equinox.gui.dialogs.history_diff_dialog import HistoryDiffDialog
from equinox.gui.error_presenter import ErrorPresenter
from equinox.gui.theme import Colors
from equinox.gui.ui_common import confirm_yes_no
from equinox.gui.ui_common import create_muted_label
from equinox.gui.ui_common import create_panel_layout
from equinox.storage import Database
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import QPoint
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QDialogButtonBox
from PyQt6.QtWidgets import QDoubleSpinBox
from PyQt6.QtWidgets import QGridLayout
from PyQt6.QtWidgets import QGroupBox
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QListWidget
from PyQt6.QtWidgets import QListWidgetItem
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QSpinBox
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

__all__ = ["HistoryPanel"]

logger = logging.getLogger(__name__)

HistoryEntry = dict[str, Any]
ContextActionSpec = tuple[str, str, Callable[[], None], bool]

# ── Module-level constants ────────────────────────────────────────────────────

_AUTO_REFRESH_INTERVAL_MS = 30_000


# ── History panel ─────────────────────────────────────────────────────────────


class HistoryPanel(QWidget):
    """Panel for viewing request history."""

    history_selected = pyqtSignal(int)  # load into editor
    history_replay = pyqtSignal(int)  # load + immediately send

    def __init__(
        self,
        db: Database,
        parent: QWidget | None = None,
        history_facade: HistoryFacade | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self._history = history_facade or HistoryFacade(db)
        self.auto_refresh_enabled = True
        self._init_ui()
        self._setup_auto_refresh()
        self.refresh()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        """Initialize the full UI layout."""
        layout = create_panel_layout(self)

        layout.addLayout(self._build_toolbar_row1())
        layout.addLayout(self._build_toolbar_row2())
        layout.addLayout(self._build_search_row())
        layout.addWidget(self._build_advanced_toggle())
        layout.addWidget(self._build_advanced_filters())
        layout.addWidget(self._build_filter_error_label())
        layout.addWidget(self._build_list_widget())
        layout.addLayout(self._build_bottom_buttons())
        layout.addWidget(self._build_stats_label())

        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)

    def _build_toolbar_row1(self) -> QHBoxLayout:
        """Refresh, clear, and auto-refresh — the controls used most often."""
        toolbar = QHBoxLayout()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Refresh History")
        self.refresh_btn.clicked.connect(self.refresh)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Clear All History")
        self.clear_btn.clicked.connect(self._clear_history)

        self.auto_refresh_checkbox = QCheckBox("Auto")
        self.auto_refresh_checkbox.setToolTip("Auto-refresh")
        self.auto_refresh_checkbox.setChecked(self.auto_refresh_enabled)
        self.auto_refresh_checkbox.stateChanged.connect(self._toggle_auto_refresh)

        for widget in (self.refresh_btn, self.clear_btn, self.auto_refresh_checkbox):
            toolbar.addWidget(widget)

        toolbar.addStretch()
        self._toolbar_row1 = toolbar
        return toolbar

    def _build_toolbar_row2(self) -> QHBoxLayout:
        """Selection-dependent actions: delete, compare, and cleanup.

        Split from row 1 because six controls (the two rows combined) never
        fit one row in the sidebar's ~300px width, even with every label
        shortened to a single word.
        """
        toolbar = QHBoxLayout()

        self.delete_sel_btn = QPushButton("Delete")
        self.delete_sel_btn.setToolTip("Delete Selected")
        self.delete_sel_btn.setEnabled(False)
        self.delete_sel_btn.clicked.connect(self._delete_selected)

        self.compare_btn = QPushButton("Compare")
        self.compare_btn.setEnabled(False)
        self.compare_btn.setToolTip(
            "Compare 2 Selected: open a side-by-side diff of two selected history entries",
        )
        self.compare_btn.clicked.connect(self._compare_selected)

        self.cleanup_btn = QPushButton("Clean…")
        self.cleanup_btn.setToolTip("Clean up: delete history entries older than N days")
        self.cleanup_btn.clicked.connect(self._cleanup_history)

        for widget in (self.delete_sel_btn, self.compare_btn, self.cleanup_btn):
            toolbar.addWidget(widget)

        toolbar.addStretch()
        self._toolbar_row2 = toolbar
        return toolbar

    def _build_search_row(self) -> QHBoxLayout:
        """Create the search and basic filter row."""
        row = QHBoxLayout()
        row.setSpacing(4)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search URL or body…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_changed)

        self.method_filter = QComboBox()
        self.method_filter.addItems(["All Methods", "GET", "POST", "PUT", "PATCH", "DELETE"])
        self.method_filter.currentTextChanged.connect(self._apply_filters)

        self.status_filter = QComboBox()
        self.status_filter.addItems(["All Status", "2xx", "3xx", "4xx", "5xx", "Errors"])
        self.status_filter.currentTextChanged.connect(self._apply_filters)

        row.addWidget(self.search_input, 2)
        row.addWidget(self.method_filter)
        row.addWidget(self.status_filter)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filters)

        return row

    def _build_advanced_toggle(self) -> QPushButton:
        """Create the toggle button for advanced filters."""
        self.advanced_toggle = QPushButton("▶ Advanced Filters")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setFlat(True)
        self.advanced_toggle.toggled.connect(self._toggle_advanced_filters)
        return self.advanced_toggle

    def _build_advanced_filters(self) -> QGroupBox:
        """Create the collapsible advanced filter section."""
        self.advanced_group = QGroupBox()
        self.advanced_group.setVisible(False)

        layout = QGridLayout(self.advanced_group)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Body regex
        layout.addWidget(QLabel("Body regex:"), 0, 0)
        self.regex_input = QLineEdit()
        self.regex_input.setPlaceholderText("e.g. error.*timeout")
        self.regex_input.setClearButtonEnabled(True)
        self.regex_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.regex_input, 0, 1, 1, 3)

        # JSONPath
        layout.addWidget(QLabel("JSONPath:"), 1, 0)
        self.jsonpath_input = QLineEdit()
        self.jsonpath_input.setPlaceholderText("e.g. $.data[*].id")
        self.jsonpath_input.setClearButtonEnabled(True)
        self.jsonpath_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.jsonpath_input, 1, 1)

        layout.addWidget(QLabel("= value:"), 1, 2)
        self.jsonpath_value_input = QLineEdit()
        self.jsonpath_value_input.setPlaceholderText("(optional)")
        self.jsonpath_value_input.setClearButtonEnabled(True)
        self.jsonpath_value_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.jsonpath_value_input, 1, 3)

        # Content-Type / Header
        layout.addWidget(QLabel("Content-Type:"), 2, 0)
        self.content_type_input = QLineEdit()
        self.content_type_input.setPlaceholderText("e.g. json")
        self.content_type_input.setClearButtonEnabled(True)
        self.content_type_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.content_type_input, 2, 1)

        layout.addWidget(QLabel("Header:"), 2, 2)
        self.header_input = QLineEdit()
        self.header_input.setPlaceholderText("Name: value")
        self.header_input.setClearButtonEnabled(True)
        self.header_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.header_input, 2, 3)

        # Time range
        layout.addWidget(QLabel("Time (s):"), 3, 0)
        time_row = QHBoxLayout()

        self.min_elapsed_spin = QDoubleSpinBox()
        self.min_elapsed_spin.setRange(0.0, 999.0)
        self.min_elapsed_spin.setDecimals(3)
        self.min_elapsed_spin.setSpecialValueText("min")
        self.min_elapsed_spin.valueChanged.connect(self._apply_filters)

        self.max_elapsed_spin = QDoubleSpinBox()
        self.max_elapsed_spin.setRange(0.0, 999.0)
        self.max_elapsed_spin.setDecimals(3)
        self.max_elapsed_spin.setSpecialValueText("max")
        self.max_elapsed_spin.valueChanged.connect(self._apply_filters)

        time_row.addWidget(self.min_elapsed_spin)
        time_row.addWidget(QLabel("–"))
        time_row.addWidget(self.max_elapsed_spin)

        layout.addLayout(time_row, 3, 1, 1, 3)

        return self.advanced_group

    def _build_filter_error_label(self) -> QLabel:
        """Create the label used to display filter validation errors."""
        self.filter_error_label = QLabel()
        self.filter_error_label.setVisible(False)
        return self.filter_error_label

    def _build_list_widget(self) -> QListWidget:
        """Create the main history list widget."""
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        return self.list_widget

    def _build_bottom_buttons(self) -> QHBoxLayout:
        """Create the row with Open and Replay buttons."""
        row = QHBoxLayout()

        self.open_btn = QPushButton("Open in Editor")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_selected)

        self.replay_btn = QPushButton("▶  Replay")
        self.replay_btn.setEnabled(False)
        self.replay_btn.setToolTip("Re-send this request immediately")
        self.replay_btn.clicked.connect(self._replay_selected)

        row.addWidget(self.open_btn)
        row.addWidget(self.replay_btn)
        row.addStretch()
        return row

    def _build_stats_label(self) -> QLabel:
        """Create the muted stats label."""
        self.stats_label = create_muted_label()
        return self.stats_label

    # ── Auto-refresh ──────────────────────────────────────────────────────────

    def _setup_auto_refresh(self) -> None:
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_if_visible)
        self.refresh_timer.start(_AUTO_REFRESH_INTERVAL_MS)

    def _refresh_if_visible(self) -> None:
        if self.isVisible():
            self.refresh()

    def _toggle_auto_refresh(self, state: int) -> None:
        self.auto_refresh_enabled = bool(state)
        if self.auto_refresh_enabled:
            self.refresh_timer.start(_AUTO_REFRESH_INTERVAL_MS)
        else:
            self.refresh_timer.stop()

    # ── Advanced-filter toggle ────────────────────────────────────────────────

    def _toggle_advanced_filters(self, checked: bool) -> None:
        self.advanced_group.setVisible(checked)
        self.advanced_toggle.setText("▼ Advanced Filters" if checked else "▶ Advanced Filters")
        if not checked:
            # Clear advanced fields when collapsing
            self.regex_input.clear()
            self.jsonpath_input.clear()
            self.jsonpath_value_input.clear()
            self.content_type_input.clear()
            self.header_input.clear()
            self.min_elapsed_spin.setValue(0.0)
            self.max_elapsed_spin.setValue(0.0)
            self.filter_error_label.setVisible(False)
            self._apply_filters()

    # ── Selection handling ────────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        ids = self._selected_real_ids()
        has = bool(ids)
        self.open_btn.setEnabled(has)
        self.replay_btn.setEnabled(has)
        self.delete_sel_btn.setEnabled(has)
        self.compare_btn.setEnabled(len(ids) == 2)

    # ── Search / filter ───────────────────────────────────────────────────────

    def _on_search_changed(self, _text: str = "") -> None:
        """Debounce typed search input by 300 ms before applying filters."""
        self._search_timer.start(300)

    def _apply_filters(self) -> None:
        self.filter_error_label.setVisible(False)

        method = self.method_filter.currentText()
        method = "" if method == "All Methods" else method
        status = self.status_filter.currentText()
        status = "" if status == "All Status" else status.lower()
        query = self.search_input.text().strip()

        # Advanced filter values — widgets are always present after _init_ui().
        body_regex = self.regex_input.text().strip()
        jsonpath = self.jsonpath_input.text().strip()
        jsonpath_value = self.jsonpath_value_input.text().strip() or None
        content_type = self.content_type_input.text().strip()
        header = self.header_input.text().strip()
        min_elapsed = self.min_elapsed_spin.value() or None
        max_elapsed = self.max_elapsed_spin.value() or None

        try:
            entries = self._history.search_history(
                query=query,
                method=method,
                status_class=status,
                body_regex=body_regex,
                jsonpath=jsonpath,
                jsonpath_value=jsonpath_value,
                content_type=content_type,
                header=header,
                min_elapsed=min_elapsed,
                max_elapsed=max_elapsed,
            )
        except Exception as exc:
            logger.warning("History filter error: %s", exc)
            self.filter_error_label.setText(str(exc))
            self.filter_error_label.setVisible(True)
            return

        self._populate_list(entries)

    # ── List population ───────────────────────────────────────────────────────

    @staticmethod
    def _date_group_label(entry_date: date) -> str:
        """Return a human-readable group label for a history entry date."""
        today = date.today()
        if entry_date == today:
            return "Today"
        if entry_date == today - timedelta(days=1):
            return "Yesterday"
        if entry_date >= today - timedelta(days=6):
            return "Last 7 Days"
        return entry_date.strftime("%B %Y")

    def _add_date_separator(self, label: str) -> None:
        """Add a non-selectable date separator row to the list."""
        sep = QListWidgetItem(f"  {label}")
        sep.setFlags(Qt.ItemFlag.NoItemFlags)
        bold = QFont()
        bold.setBold(True)
        sep.setFont(bold)
        sep.setForeground(QColor(Colors.FG_MUTED))
        sep.setBackground(QColor(Colors.BG_ALT))
        self.list_widget.addItem(sep)

    def _populate_list(self, entries: list[HistoryEntry]) -> None:
        """Rebuild the list widget from *entries*, restoring the prior selection.

        Signals and screen updates are suppressed during the rebuild to avoid
        O(n) ``itemSelectionChanged`` firings and per-row repaint overhead.
        ``_on_selection_changed`` is driven manually after re-enabling.
        """
        selected_ids = {
            i.data(Qt.ItemDataRole.UserRole)
            for i in self.list_widget.selectedItems()
            if i.data(Qt.ItemDataRole.UserRole) is not None
        }

        self.list_widget.blockSignals(True)
        self.list_widget.setUpdatesEnabled(False)
        try:
            self.list_widget.clear()
            current_group: str | None = None

            for entry in entries:
                ts_str = str(entry.get("executed_at", ""))
                try:
                    entry_date = datetime.fromisoformat(ts_str).date()
                except Exception:
                    entry_date = date.today()
                group_label = self._date_group_label(entry_date)
                if group_label != current_group:
                    self._add_date_separator(group_label)
                    current_group = group_label

                status = entry.get("status_code", "ERR")
                method = entry["method"]
                url = entry["url"]
                ts = ts_str.split(".")[0]
                elapsed = entry.get("elapsed")
                elapsed_str = f"  {int(elapsed * 1000)} ms" if elapsed else ""

                text = f"[{status}] {method}  {url}\n{ts}{elapsed_str}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, entry["id"])

                if entry.get("error"):
                    item.setForeground(QColor(Colors.RED))
                elif isinstance(status, int) and status >= 400:
                    item.setForeground(QColor(Colors.AMBER))
                else:
                    item.setForeground(QColor(Colors.GREEN))

                self.list_widget.addItem(item)

                if entry["id"] in selected_ids:
                    item.setSelected(True)
        finally:
            self.list_widget.setUpdatesEnabled(True)
            self.list_widget.blockSignals(False)

        # Drive button-state update manually since signals were blocked.
        self._on_selection_changed()

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Refresh history list and stats."""
        self._apply_filters()
        try:
            stats = self._history.get_stats()
            self.stats_label.setText(
                f"Total: {stats['total']}  |  "
                f"OK: {stats['successful']}  |  "
                f"Failed: {stats['failed']}",
            )
        except Exception as exc:
            logger.error("Failed to load history stats: %s", exc, exc_info=True)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        history_id = item.data(Qt.ItemDataRole.UserRole)
        logger.debug("HistoryPanel: itemDoubleClicked id=%r", history_id)
        if history_id:
            try:
                self.history_selected.emit(int(history_id))
            except Exception:
                logger.exception("Failed to emit history_selected for id=%r", history_id)

    def _open_selected(self) -> None:
        """Emit ``history_selected`` for the first real selected entry."""
        self._emit_first_selected(self.history_selected.emit)

    def _replay_selected(self) -> None:
        """Emit ``history_replay`` for the first real selected entry."""
        self._emit_first_selected(self.history_replay.emit)

    def _delete_selected(self) -> None:
        """Delete all currently selected history entries."""
        ids = self._selected_real_ids()
        if not ids:
            return
        n = len(ids)
        if not confirm_yes_no(
            self,
            "Confirm Delete",
            f"Delete {n} selected history entr{'y' if n == 1 else 'ies'}?",
        ):
            return

        errors: list[str] = []
        for hid in ids:
            try:
                self._history.delete_history(hid)
            except Exception as exc:
                logger.error("Failed to delete history id=%s: %s", hid, exc, exc_info=True)
                errors.append(str(exc))

        # Always refresh so the list reflects the actual DB state.
        self.refresh()

        if errors:
            ErrorPresenter.warning(
                self,
                f"{len(errors)} deletion(s) failed:\n\n" + "\n".join(errors),
                title="Delete Errors",
            )

    def _show_context_menu(self, position: QPoint) -> None:
        item = self.list_widget.itemAt(position)
        if not item:
            return
        history_id = item.data(Qt.ItemDataRole.UserRole)
        if history_id is None:
            return  # separator row
        menu = QMenu()
        action_specs: list[ContextActionSpec] = [
            (
                "open_in_editor",
                "Open in Editor",
                lambda: self.history_selected.emit(int(history_id)),
                False,
            ),
            (
                "edit_replay",
                "Edit && Replay…",
                lambda: self.history_selected.emit(int(history_id)),
                False,
            ),
            (
                "replay",
                "▶  Replay",
                lambda: self.history_replay.emit(int(history_id)),
                False,
            ),
            (
                "delete",
                "Delete",
                lambda: self._delete_one(history_id),
                True,
            ),
        ]
        ordered = self._ordered_context_actions("history_item", action_specs)
        added_destructive_separator = False
        for action_id, label, callback, is_destructive in ordered:
            if is_destructive and not added_destructive_separator:
                menu.addSeparator()
                added_destructive_separator = True
            action = QAction(label, self)
            if action_id == "edit_replay":
                action.setToolTip("Load into editor for modification before sending")
            action.triggered.connect(
                lambda _checked=False, aid=action_id, cb=callback: self._run_context_action(
                    "history_item",
                    aid,
                    cb,
                ),
            )
            menu.addAction(action)
        viewport = self.list_widget.viewport()
        if viewport is None:
            return
        menu.exec(viewport.mapToGlobal(position))

    def _context_action_usage_count(self, context: str, action_id: str) -> int:
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

    def _run_context_action(
        self,
        context: str,
        action_id: str,
        callback: Callable[[], None],
    ) -> None:
        self._record_context_action_usage(context, action_id)
        callback()

    def _ordered_context_actions(
        self,
        context: str,
        action_specs: list[ContextActionSpec],
    ) -> list[ContextActionSpec]:
        """Sort non-destructive actions by usage while keeping destructive actions last."""
        safe = []
        destructive = []
        for idx, spec in enumerate(action_specs):
            action_id, label, callback, is_destructive = spec
            if is_destructive:
                destructive.append((idx, spec))
                continue
            count = self._context_action_usage_count(context, action_id)
            safe.append((-count, idx, spec))
        safe.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in safe] + [row[1] for row in destructive]

    def _delete_one(self, history_id: int) -> None:
        """Delete a single history entry (via context menu)."""
        try:
            self._history.delete_history(history_id)
        except Exception as exc:
            logger.error("Failed to delete history id=%s: %s", history_id, exc, exc_info=True)
            ErrorPresenter.warning(self, str(exc), title="Delete Error")
            return
        self.refresh()

    def _compare_selected(self) -> None:
        """Open a side-by-side diff for the two currently selected history entries."""
        ids = self._selected_real_ids()
        if len(ids) != 2:
            return
        id_a, id_b = ids
        try:
            entry_a = self._history.get_history(id_a)
            entry_b = self._history.get_history(id_b)
            if entry_a and entry_b:
                HistoryDiffDialog(entry_a, entry_b, self).exec()
            else:
                logger.warning("Could not load history entries for comparison: %r %r", id_a, id_b)
        except Exception as exc:
            ErrorPresenter.error(
                self,
                "Failed to load history entries.",
                details=str(exc),
            )

    def _clear_history(self) -> None:
        if not confirm_yes_no(self, "Confirm Clear", "Clear all history?"):
            return
        try:
            self._history.clear_history()
        except Exception as exc:
            logger.error("Failed to clear history: %s", exc, exc_info=True)
            ErrorPresenter.warning(self, str(exc))
            return
        self.refresh()

    def _cleanup_history(self) -> None:
        """Delete history entries older than a user-chosen number of days."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Clean Up History")
        dialog.setFixedWidth(320)
        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.addWidget(QLabel("Delete entries older than:"))

        spin = QSpinBox()
        spin.setRange(1, 3650)
        spin.setValue(30)
        spin.setSuffix(" days")
        dlg_layout.addWidget(spin)

        info = QLabel("This cannot be undone.")
        dlg_layout.addWidget(info)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        dlg_layout.addWidget(btns)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        days = spin.value()
        try:
            self._history.clear_history(days=days)
        except Exception as exc:
            logger.error("Failed to clean up history (days=%d): %s", days, exc, exc_info=True)
            ErrorPresenter.error(
                self,
                "Failed to clean up history.",
                details=str(exc),
            )
            return
        self.refresh()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _selected_real_ids(self) -> list[int]:
        """Return the DB IDs of all selected non-separator list entries.

        Separator items have no ``UserRole`` data; they are excluded so callers
        never have to guard against ``None`` IDs themselves.
        """
        return [
            int(i.data(Qt.ItemDataRole.UserRole))
            for i in self.list_widget.selectedItems()
            if i.data(Qt.ItemDataRole.UserRole) is not None
        ]

    def _emit_first_selected(self, signal: Callable[[int], None]) -> None:
        """Emit *signal* with the ID of the first real selected list entry.

        Centralises the identical logic shared by ``_open_selected`` and
        ``_replay_selected``, which differ only in which signal they emit.
        """
        for it in self.list_widget.selectedItems():
            hid = it.data(Qt.ItemDataRole.UserRole)
            if hid is not None:
                try:
                    signal(int(hid))
                except Exception:
                    logger.exception("Failed to emit signal for history id %r", hid)
                return
