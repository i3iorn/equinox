"""History panel"""
from __future__ import annotations

import difflib
import logging
from datetime import date, datetime, timedelta

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QTextCharFormat
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QSpinBox, QSplitter,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from equinox.gui.theme import Colors
from equinox.storage import Database, HistoryManager

__all__ = ["HistoryPanel", "HistoryDiffDialog"]

logger = logging.getLogger(__name__)

# ── Module-level constants ────────────────────────────────────────────────────

_AUTO_REFRESH_INTERVAL_MS = 30_000


# ── Helper functions ──────────────────────────────────────────────────────────


def _bold_font() -> QFont:
    f = QFont()
    f.setBold(True)
    return f


def _mono_font() -> QFont:
    return QFont("Courier New", 9)


# ── History panel ─────────────────────────────────────────────────────────────


class HistoryPanel(QWidget):
    """Panel for viewing request history."""

    history_selected = pyqtSignal(int)   # load into editor
    history_replay   = pyqtSignal(int)   # load + immediately send

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        # Cache the manager — it is a lightweight DB wrapper; no need to
        # reconstruct it on every operation.
        self._mgr = HistoryManager(db)
        self.auto_refresh_enabled = True
        self._init_ui()
        self._setup_auto_refresh()
        self.refresh()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.clicked.connect(self._clear_history)
        self.delete_sel_btn = QPushButton("Delete Selected")
        self.delete_sel_btn.setEnabled(False)
        self.delete_sel_btn.clicked.connect(self._delete_selected)
        self.compare_btn = QPushButton("Compare 2 Selected")
        self.compare_btn.setEnabled(False)
        self.compare_btn.setToolTip("Open a side-by-side diff of two selected history entries")
        self.compare_btn.clicked.connect(self._compare_selected)

        self.auto_refresh_checkbox = QCheckBox("Auto-refresh")
        self.auto_refresh_checkbox.setChecked(self.auto_refresh_enabled)
        self.auto_refresh_checkbox.stateChanged.connect(self._toggle_auto_refresh)

        self.cleanup_btn = QPushButton("Clean up…")
        self.cleanup_btn.setToolTip("Delete history entries older than N days")
        self.cleanup_btn.clicked.connect(self._cleanup_history)

        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.clear_btn)
        toolbar.addWidget(self.delete_sel_btn)
        toolbar.addWidget(self.compare_btn)
        toolbar.addWidget(self.cleanup_btn)
        toolbar.addWidget(self.auto_refresh_checkbox)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # ── Search / filter row ───────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search URL or body…")
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.setClearButtonEnabled(True)

        self.method_filter = QComboBox()
        self.method_filter.addItems(["All Methods", "GET", "POST", "PUT", "PATCH", "DELETE"])
        self.method_filter.currentTextChanged.connect(self._apply_filters)

        self.status_filter = QComboBox()
        self.status_filter.addItems(["All Status", "2xx", "3xx", "4xx", "5xx", "Errors"])
        self.status_filter.currentTextChanged.connect(self._apply_filters)

        search_row.addWidget(self.search_input, 2)
        search_row.addWidget(self.method_filter)
        search_row.addWidget(self.status_filter)
        layout.addLayout(search_row)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filters)

        # ── Advanced filters (collapsible) ────────────────────────────
        self.advanced_toggle = QPushButton("▶ Advanced Filters")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setFlat(True)
        self.advanced_toggle.toggled.connect(self._toggle_advanced_filters)
        layout.addWidget(self.advanced_toggle)

        self.advanced_group = QGroupBox()
        self.advanced_group.setVisible(False)
        adv_layout = QGridLayout(self.advanced_group)
        adv_layout.setContentsMargins(4, 4, 4, 4)
        adv_layout.setSpacing(4)

        # Row 0: Body regex
        adv_layout.addWidget(QLabel("Body regex:"), 0, 0)
        self.regex_input = QLineEdit()
        self.regex_input.setPlaceholderText("e.g. error.*timeout")
        self.regex_input.setClearButtonEnabled(True)
        self.regex_input.textChanged.connect(self._on_search_changed)
        adv_layout.addWidget(self.regex_input, 0, 1, 1, 3)

        # Row 1: JSONPath
        adv_layout.addWidget(QLabel("JSONPath:"), 1, 0)
        self.jsonpath_input = QLineEdit()
        self.jsonpath_input.setPlaceholderText("e.g. $.data[*].id")
        self.jsonpath_input.setClearButtonEnabled(True)
        self.jsonpath_input.textChanged.connect(self._on_search_changed)
        adv_layout.addWidget(self.jsonpath_input, 1, 1)

        adv_layout.addWidget(QLabel("= value:"), 1, 2)
        self.jsonpath_value_input = QLineEdit()
        self.jsonpath_value_input.setPlaceholderText("(optional)")
        self.jsonpath_value_input.setClearButtonEnabled(True)
        self.jsonpath_value_input.textChanged.connect(self._on_search_changed)
        adv_layout.addWidget(self.jsonpath_value_input, 1, 3)

        # Row 2: Content-Type and Header
        adv_layout.addWidget(QLabel("Content-Type:"), 2, 0)
        self.content_type_input = QLineEdit()
        self.content_type_input.setPlaceholderText("e.g. json")
        self.content_type_input.setClearButtonEnabled(True)
        self.content_type_input.textChanged.connect(self._on_search_changed)
        adv_layout.addWidget(self.content_type_input, 2, 1)

        adv_layout.addWidget(QLabel("Header:"), 2, 2)
        self.header_input = QLineEdit()
        self.header_input.setPlaceholderText("Name: value")
        self.header_input.setClearButtonEnabled(True)
        self.header_input.textChanged.connect(self._on_search_changed)
        adv_layout.addWidget(self.header_input, 2, 3)

        # Row 3: Elapsed time range
        adv_layout.addWidget(QLabel("Time (s):"), 3, 0)
        time_row = QHBoxLayout()
        self.min_elapsed_spin = QDoubleSpinBox()
        self.min_elapsed_spin.setRange(0.0, 999.0)
        self.min_elapsed_spin.setDecimals(3)
        self.min_elapsed_spin.setSpecialValueText("min")
        self.min_elapsed_spin.setValue(0.0)
        self.min_elapsed_spin.valueChanged.connect(self._apply_filters)

        self.max_elapsed_spin = QDoubleSpinBox()
        self.max_elapsed_spin.setRange(0.0, 999.0)
        self.max_elapsed_spin.setDecimals(3)
        self.max_elapsed_spin.setSpecialValueText("max")
        self.max_elapsed_spin.setValue(0.0)
        self.max_elapsed_spin.valueChanged.connect(self._apply_filters)

        time_row.addWidget(self.min_elapsed_spin)
        time_row.addWidget(QLabel("–"))
        time_row.addWidget(self.max_elapsed_spin)
        adv_layout.addLayout(time_row, 3, 1, 1, 3)

        layout.addWidget(self.advanced_group)

        # Validation label (for regex / JSONPath errors)
        self.filter_error_label = QLabel()
        self.filter_error_label.setStyleSheet("color: red; font-size: 11px;")
        self.filter_error_label.setVisible(False)
        layout.addWidget(self.filter_error_label)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.list_widget)

        # Replay / Open buttons beneath the list
        btn_row = QHBoxLayout()
        self.open_btn   = QPushButton("Open in Editor")
        self.replay_btn = QPushButton("▶  Replay")
        self.replay_btn.setToolTip("Re-send this request immediately")
        self.open_btn.setEnabled(False)
        self.replay_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_selected)
        self.replay_btn.clicked.connect(self._replay_selected)
        btn_row.addWidget(self.open_btn)
        btn_row.addWidget(self.replay_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.stats_label = QLabel()
        self.stats_label.setObjectName("mutedLabel")
        layout.addWidget(self.stats_label)

        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)

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
        self.advanced_toggle.setText(
            "▼ Advanced Filters" if checked else "▶ Advanced Filters"
        )
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
        sel = self.list_widget.selectedItems()
        # Only count real entries (not separator labels)
        real_sel = [i for i in sel if i.data(Qt.ItemDataRole.UserRole) is not None]
        has = bool(real_sel)
        self.open_btn.setEnabled(has)
        self.replay_btn.setEnabled(has)
        self.delete_sel_btn.setEnabled(has)
        self.compare_btn.setEnabled(len(real_sel) == 2)

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
        query  = self.search_input.text().strip()

        # Advanced filter values — widgets are always present after _init_ui().
        body_regex     = self.regex_input.text().strip()
        jsonpath       = self.jsonpath_input.text().strip()
        jsonpath_value = self.jsonpath_value_input.text().strip() or None
        content_type   = self.content_type_input.text().strip()
        header         = self.header_input.text().strip()
        min_elapsed    = self.min_elapsed_spin.value() or None
        max_elapsed    = self.max_elapsed_spin.value() or None

        try:
            entries = self._mgr.search_history(
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
            # Mark the offending input with a red border
            self.regex_input.setStyleSheet(
                "border: 1px solid red;" if body_regex and "regex" in str(exc).lower() else ""
            )
            self.jsonpath_input.setStyleSheet(
                "border: 1px solid red;" if jsonpath and "jsonpath" in str(exc).lower() else ""
            )
            return

        # Clear any previous error styling
        self.regex_input.setStyleSheet("")
        self.jsonpath_input.setStyleSheet("")

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
        sep.setFont(_bold_font())
        sep.setForeground(QColor(Colors.FG_MUTED))
        sep.setBackground(QColor(Colors.BG_ALT))
        self.list_widget.addItem(sep)

    def _populate_list(self, entries: list[dict]) -> None:
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

                status  = entry.get("status_code", "ERR")
                method  = entry["method"]
                url     = entry["url"]
                ts      = ts_str.split(".")[0]
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
            stats = self._mgr.get_stats()
            self.stats_label.setText(
                f"Total: {stats['total']}  |  "
                f"OK: {stats['successful']}  |  "
                f"Failed: {stats['failed']}"
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
        for it in self.list_widget.selectedItems():
            hid = it.data(Qt.ItemDataRole.UserRole)
            if hid is not None:
                try:
                    self.history_selected.emit(int(hid))
                except Exception:
                    logger.exception("_open_selected: invalid history id %r", hid)
                return

    def _replay_selected(self) -> None:
        """Emit ``history_replay`` for the first real selected entry."""
        for it in self.list_widget.selectedItems():
            hid = it.data(Qt.ItemDataRole.UserRole)
            if hid is not None:
                try:
                    self.history_replay.emit(int(hid))
                except Exception:
                    logger.exception("_replay_selected: invalid history id %r", hid)
                return

    def _delete_selected(self) -> None:
        """Delete all currently selected history entries."""
        ids = [
            i.data(Qt.ItemDataRole.UserRole)
            for i in self.list_widget.selectedItems()
            if i.data(Qt.ItemDataRole.UserRole) is not None
        ]
        if not ids:
            return
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete {len(ids)} selected history entr{'y' if len(ids) == 1 else 'ies'}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        errors: list[str] = []
        for hid in ids:
            try:
                self._mgr.delete_history(hid)
            except Exception as exc:
                logger.error("Failed to delete history id=%s: %s", hid, exc, exc_info=True)
                errors.append(str(exc))

        # Always refresh so the list reflects the actual DB state.
        self.refresh()

        if errors:
            QMessageBox.warning(
                self, "Delete Errors",
                f"{len(errors)} deletion(s) failed:\n\n" + "\n".join(errors),
            )

    def _show_context_menu(self, position) -> None:
        item = self.list_widget.itemAt(position)
        if not item:
            return
        history_id = item.data(Qt.ItemDataRole.UserRole)
        if history_id is None:
            return  # separator row
        menu = QMenu()
        open_action        = QAction("Open in Editor", self)
        edit_replay_action = QAction("Edit && Replay…", self)
        edit_replay_action.setToolTip("Load into editor for modification before sending")
        replay_action      = QAction("▶  Replay", self)
        delete_action      = QAction("Delete", self)
        open_action.triggered.connect(lambda: self.history_selected.emit(history_id))
        edit_replay_action.triggered.connect(lambda: self.history_selected.emit(history_id))
        replay_action.triggered.connect(lambda: self.history_replay.emit(history_id))
        delete_action.triggered.connect(lambda: self._delete_one(history_id))
        menu.addAction(open_action)
        menu.addAction(edit_replay_action)
        menu.addAction(replay_action)
        menu.addSeparator()
        menu.addAction(delete_action)
        menu.exec(self.list_widget.viewport().mapToGlobal(position))

    def _delete_one(self, history_id: int) -> None:
        """Delete a single history entry (via context menu)."""
        try:
            self._mgr.delete_history(history_id)
        except Exception as exc:
            logger.error("Failed to delete history id=%s: %s", history_id, exc, exc_info=True)
            QMessageBox.warning(self, "Error", str(exc))
            return
        self.refresh()

    def _compare_selected(self) -> None:
        """Open a side-by-side diff for the two currently selected history entries."""
        real_items = [
            i for i in self.list_widget.selectedItems()
            if i.data(Qt.ItemDataRole.UserRole) is not None
        ]
        if len(real_items) != 2:
            return
        id_a = real_items[0].data(Qt.ItemDataRole.UserRole)
        id_b = real_items[1].data(Qt.ItemDataRole.UserRole)
        try:
            entry_a = self._mgr.get_history(id_a)
            entry_b = self._mgr.get_history(id_b)
            if entry_a and entry_b:
                HistoryDiffDialog(entry_a, entry_b, self).exec()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load history entries:\n{exc}")

    def _clear_history(self) -> None:
        reply = QMessageBox.question(
            self, "Confirm Clear", "Clear all history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._mgr.clear_history()
        except Exception as exc:
            logger.error("Failed to clear history: %s", exc, exc_info=True)
            QMessageBox.warning(self, "Error", str(exc))
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
        info.setStyleSheet("color: grey; font-size: 11px;")
        dlg_layout.addWidget(info)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        dlg_layout.addWidget(btns)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        days = spin.value()
        try:
            self._mgr.clear_history(days=days)
        except Exception as exc:
            logger.error("Failed to clean up history (days=%d): %s", days, exc, exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to clean up history:\n{exc}")
            return
        self.refresh()


# ── Diff dialog ───────────────────────────────────────────────────────────────


class HistoryDiffDialog(QDialog):
    """Side-by-side diff of two history entries (request and response)."""

    def __init__(
        self,
        entry_a: dict,
        entry_b: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._entry_a = entry_a
        self._entry_b = entry_b
        self.setWindowTitle(
            f"Compare  [{entry_a.get('method')} {entry_a.get('url', '')[:40]}]  "
            f"vs  [{entry_b.get('method')} {entry_b.get('url', '')[:40]}]"
        )
        self.setMinimumSize(980, 640)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(
            self._make_split_widget(
                self._format_request(self._entry_a),
                self._format_request(self._entry_b),
                label_a=f"Entry #{self._entry_a.get('id')}",
                label_b=f"Entry #{self._entry_b.get('id')}",
            ),
            "Request",
        )
        tabs.addTab(
            self._make_split_widget(
                self._format_response(self._entry_a),
                self._format_response(self._entry_b),
                label_a=f"Entry #{self._entry_a.get('id')}",
                label_b=f"Entry #{self._entry_b.get('id')}",
            ),
            "Response",
        )
        tabs.addTab(self._make_unified_diff_widget(), "Unified Diff (response body)")

        layout.addWidget(tabs, 1)

        close_btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btns.rejected.connect(self.reject)
        layout.addWidget(close_btns)

    # ── Formatters ────────────────────────────────────────────────────────────

    @staticmethod
    def _format_request(entry: dict) -> str:
        lines = [
            f"{entry.get('method', '?')} {entry.get('url', '?')}",
            f"Timestamp : {entry.get('executed_at', '?')}",
            "",
            "── Headers ──",
        ]
        for k, v in (entry.get("request_headers") or {}).items():
            lines.append(f"  {k}: {v}")
        lines += ["", "── Body ──", entry.get("request_body") or "(none)"]
        return "\n".join(lines)

    @staticmethod
    def _format_response(entry: dict) -> str:
        lines = [
            f"Status  : {entry.get('status_code', '?')} {entry.get('reason', '')}",
            f"Elapsed : {int((entry.get('elapsed') or 0) * 1000)} ms",
            "",
            "── Headers ──",
        ]
        for k, v in (entry.get("response_headers") or {}).items():
            lines.append(f"  {k}: {v}")
        lines += ["", "── Body ──", entry.get("response_body") or "(none)"]
        return "\n".join(lines)

    # ── Widgets ───────────────────────────────────────────────────────────────

    def _make_split_widget(
        self,
        text_a: str,
        text_b: str,
        label_a: str = "A",
        label_b: str = "B",
    ) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        hdr = QHBoxLayout()
        for lbl in (label_a, label_b):
            lbl_widget = QLabel(f"<b>{lbl}</b>")
            lbl_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hdr.addWidget(lbl_widget, 1)
        lay.addLayout(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        for text in (text_a, text_b):
            te = QTextEdit()
            te.setReadOnly(True)
            te.setFont(_mono_font())
            te.setPlainText(text)
            splitter.addWidget(te)
        splitter.setSizes([490, 490])
        lay.addWidget(splitter, 1)
        return w

    def _make_unified_diff_widget(self) -> QWidget:
        body_a = (self._entry_a.get("response_body") or "").splitlines(keepends=True)
        body_b = (self._entry_b.get("response_body") or "").splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            body_a, body_b,
            fromfile=f"Entry #{self._entry_a.get('id')}",
            tofile=f"Entry #{self._entry_b.get('id')}",
            lineterm="",
        ))

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        te = QTextEdit()
        te.setReadOnly(True)
        te.setFont(_mono_font())

        if not diff_lines:
            te.setPlainText("(no differences in response bodies)")
        else:
            fmt_add = QTextCharFormat()
            fmt_add.setBackground(QColor("#1a3a1a"))
            fmt_rem = QTextCharFormat()
            fmt_rem.setBackground(QColor("#3a1a1a"))
            fmt_hdr = QTextCharFormat()
            fmt_hdr.setForeground(QColor(Colors.BLUE))
            fmt_def = QTextCharFormat()

            cursor = te.textCursor()
            for line in diff_lines:
                if line.startswith("+++") or line.startswith("---"):
                    cursor.insertText(line + "\n", fmt_hdr)
                elif line.startswith("+"):
                    cursor.insertText(line + "\n", fmt_add)
                elif line.startswith("-"):
                    cursor.insertText(line + "\n", fmt_rem)
                else:
                    cursor.insertText(line + "\n", fmt_def)

        lay.addWidget(te, 1)
        return w
