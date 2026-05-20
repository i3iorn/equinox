"""Body-related mixin for RequestPanel: captures, assertions, multipart, load/clear.

Provides comprehensive request body handling including:
- Captures: Extract values from responses into variables
- Assertions: Define pass/fail rules for responses
- Multipart: Handle multipart/form-data bodies
- Body type detection and switching
- Body search and highlighting (text, regex, JSONPath)
- Request loading and clearing

All UI operations are guarded against missing widgets (headless/test environments).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from equinox.core.assertions import evaluate_assertion as _evaluate_assertion
from equinox.core.request import Request
from equinox.gui.file_ops import validate_selected_path
from equinox.gui.theme import get_mono_font
from equinox.gui.workers import DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Module-level Constants
# ──────────────────────────────────────────────────────────────────────────────

# Ordered tuples used to populate combo boxes — single source of truth
_CAPTURE_SOURCES: tuple[str, ...] = ("json", "header", "regex", "status")
_ASSERTION_TYPES: tuple[str, ...] = (
    "status",
    "body_contains",
    "header_value",
    "jsonpath",
    "elapsed_lt",
)

# JSONPath value preview character limit
_JSONPATH_PREVIEW_CHARS: int = 50

# Maximum body search highlights to keep UI responsive
_MAX_HIGHLIGHTS: int = 500

# UI Configuration
_TOOLBAR_SPACING = 2
_TOOLBAR_MARGINS = (0, 2, 0, 0)
_BUTTON_ADD_WIDTH = 64
_BUTTON_REMOVE_WIDTH = 80
_LAYOUT_MARGINS = (0, 4, 0, 0)

# Tab labels for captures/assertions
_LABEL_CAPTURES = "Captures"
_LABEL_ASSERTIONS = "Assertions"
_LABEL_LAST_CAPTURE = "Last capture results:"
_LABEL_LAST_ASSERTION = "Last assertion results:"
_LABEL_EMPTY = "—"


class RequestBodyMixin:
    """Methods for captures, assertions, multipart, body-type handling, load, and clear.

    Responsibilities:
    - Table management for captures and assertions
    - Request body loading and persistence
    - Search and highlighting in body
    - Multipart form data handling
    - JSONPath evaluation for captures and search
    """

    tabs: Any
    body_type_combo: QComboBox
    body_text: Any
    headers_table: Any
    params_table: Any
    path_params_table: Any
    _path_params_widget: QWidget
    _multipart_table: QTableWidget
    _mp_toolbar: QWidget
    _fmt_json_btn: QPushButton
    _gql_widget: QWidget
    _gql_query: Any
    _gql_vars: Any
    captures_table: QTableWidget
    captures_results_label: QLabel
    assertions_table: QTableWidget
    assertions_results_label: QLabel
    pre_script_editor: Any
    post_script_editor: Any
    pre_script_result: QLabel
    post_script_result: QLabel
    cert_path_input: Any
    cert_key_input: Any
    timeout_spin: Any
    verify_ssl_check: Any
    follow_redirects_check: Any
    notes_editor: Any
    url_input: Any
    method_combo: QComboBox
    _worker: Any
    _auth: Any
    _inherited_auth: Any
    _inherited_auth_source: Any
    current_request: Optional[Request]

    def _resolve_inherited_auth(self) -> None: ...
    def _update_auth_display(self, auth: Any) -> None: ...
    def _clear_dirty(self) -> None: ...
    def _update_url_suffix(self) -> None: ...
    def _cancel_request(self) -> None: ...

    # ── Internal helpers ──────────────────────────────────────────────

    @staticmethod
    def _select_range(target: QTextEdit, start: int, end: int) -> None:
        """Move cursor to select range [start, end) with bounds clamping.

        Args:
            target: QTextEdit widget
            start: Start position
            end: End position

        Silently does nothing if range invalid or widget unavailable.
        """
        try:
            doc = target.document()
            max_pos = max(0, doc.characterCount() - 1)
        except Exception:
            return

        s = max(0, min(start, max_pos))
        e = max(0, min(end, max_pos))
        if s >= e:
            return

        cursor = target.textCursor()
        cursor.setPosition(s)
        cursor.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        target.setTextCursor(cursor)

    @staticmethod
    def _try_ui(fn: Any, *args: Any, **kwargs: Any) -> None:
        """Call function and silently swallow RuntimeError (deleted C++ widget).

        Used to guard UI operations in headless/test environments where
        the underlying Qt widget may have been deleted.

        Args:
            fn: Function to call
            *args: Positional arguments
            **kwargs: Keyword arguments
        """
        try:
            fn(*args, **kwargs)
        except RuntimeError:
            logger.debug("Widget unavailable in %s", getattr(fn, "__name__", fn), exc_info=True)

    # ── Shared tab-building helpers ───────────────────────────────────

    @staticmethod
    def _build_action_tab_shell(
        title: str, add_slot: Any, remove_slot: Any
    ) -> tuple[QWidget, QVBoxLayout, QLabel]:
        """Build common outer shell for Captures/Assertions tabs.

        Creates toolbar with Add/Remove buttons and results label.

        Args:
            title: Tab title
            add_slot: Callback for add button
            remove_slot: Callback for remove button

        Returns:
            (widget, layout, results_label)
        """
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(*_LAYOUT_MARGINS)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(*_TOOLBAR_MARGINS)
        toolbar.setSpacing(_TOOLBAR_SPACING)

        lbl = QLabel(title)
        toolbar.addWidget(lbl)

        add_btn = QPushButton("+ Add")
        add_btn.setMinimumWidth(_BUTTON_ADD_WIDTH)
        add_btn.clicked.connect(add_slot)

        remove_btn = QPushButton("− Remove")
        remove_btn.setMinimumWidth(_BUTTON_REMOVE_WIDTH)
        remove_btn.clicked.connect(remove_slot)

        toolbar.addWidget(add_btn)
        toolbar.addWidget(remove_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        results_label = QLabel(_LABEL_EMPTY)
        results_label.setFont(get_mono_font())
        results_label.setWordWrap(True)
        results_label.setObjectName("mutedLabel")

        return w, layout, results_label

    @staticmethod
    def _remove_selected_from(table: QTableWidget) -> None:
        """Remove selected rows from table (highest index first).

        Args:
            table: QTableWidget to modify
        """
        for r in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(r)

    # ── Captures tab ──────────────────────────────────────────────────

    def _create_captures_tab(self) -> QWidget:
        """Create captures table tab.

        Returns:
            QWidget for captures tab
        """
        w, layout, self.captures_results_label = self._build_action_tab_shell(
            _LABEL_CAPTURES, self._captures_add_row, self._captures_remove_row
        )
        self.captures_table = self._create_captures_table()
        layout.addWidget(self.captures_table)
        layout.addWidget(QLabel(_LABEL_LAST_CAPTURE))
        layout.addWidget(self.captures_results_label)
        return w

    @staticmethod
    def _create_captures_table() -> QTableWidget:
        """Create and configure captures table.

        Returns:
            Configured QTableWidget
        """
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Variable", "Source", "Path / Pattern", "Default"])
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    # ── Assertions tab ────────────────────────────────────────────────

    def _create_assertions_tab(self) -> QWidget:
        """Create assertions table tab.

        Returns:
            QWidget for assertions tab
        """
        w, layout, self.assertions_results_label = self._build_action_tab_shell(
            _LABEL_ASSERTIONS, self._assertions_add_row, self._assertions_remove_row
        )
        self.assertions_table = self._create_assertions_table()
        layout.addWidget(self.assertions_table)
        layout.addWidget(QLabel(_LABEL_LAST_ASSERTION))
        layout.addWidget(self.assertions_results_label)
        return w

    @staticmethod
    def _create_assertions_table() -> QTableWidget:
        """Create and configure assertions table.

        Returns:
            Configured QTableWidget
        """
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Type", "Field / Path", "Expected"])
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setDefaultSectionSize(160)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    def _assertions_add_row(self) -> None:
        """Append empty assertion row to table."""
        row = self.assertions_table.rowCount()
        self.assertions_table.insertRow(row)

        type_combo = QComboBox()
        type_combo.addItems(_ASSERTION_TYPES)
        self.assertions_table.setCellWidget(row, 0, type_combo)

        self.assertions_table.setItem(row, 1, QTableWidgetItem(""))
        self.assertions_table.setItem(row, 2, QTableWidgetItem(""))

    def _assertions_remove_row(self) -> None:
        """Remove selected assertion rows."""
        self._remove_selected_from(self.assertions_table)

    def _get_assertions(self) -> list[dict[str, str]]:
        """Collect assertion rules from table.

        Returns:
            List of assertion rule dicts
        """
        rules = []
        for row in range(self.assertions_table.rowCount()):
            widget = self.assertions_table.cellWidget(row, 0)
            a_type = widget.currentText() if widget else "status"

            f_item = self.assertions_table.item(row, 1)
            e_item = self.assertions_table.item(row, 2)

            field = f_item.text().strip() if f_item else ""
            expected = e_item.text().strip() if e_item else ""

            if expected:
                rules.append({"type": a_type, "field": field, "expected": expected})

        return rules

    def _set_assertions(self, rules: list[dict[str, str]] | None) -> None:
        """Populate assertions table from rule list.

        Args:
            rules: List of assertion rule dicts or None
        """
        self.assertions_table.setRowCount(0)
        for rule in rules or []:
            self._assertions_add_row()
            row = self.assertions_table.rowCount() - 1

            widget = self.assertions_table.cellWidget(row, 0)
            if widget:
                idx = widget.findText(rule.get("type", "status"))
                if idx >= 0:
                    widget.setCurrentIndex(idx)

            f_item = self.assertions_table.item(row, 1)
            e_item = self.assertions_table.item(row, 2)

            if f_item:
                f_item.setText(rule.get("field", ""))
            if e_item:
                e_item.setText(rule.get("expected", ""))

    def _evaluate_assertions(self, response: Any) -> None:
        """Evaluate assertion rules against response.

        Args:
            response: Response object to test
        """
        rules = self._get_assertions()
        if not rules:
            self.assertions_results_label.setText(_LABEL_EMPTY)
            return

        lines = []
        for rule in rules:
            passed, msg = _evaluate_assertion(rule, response)
            icon = "✓" if passed else "✗"
            lines.append(f"{icon} {msg}")

        self.assertions_results_label.setText("\n".join(lines) if lines else _LABEL_EMPTY)

        # Update tab title with pass/fail summary
        passed_count = sum(1 for line in lines if line.startswith("✓"))
        total = len(lines)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith(_LABEL_ASSERTIONS):
                label = (
                    f"{_LABEL_ASSERTIONS} ({passed_count}/{total})" if lines else _LABEL_ASSERTIONS
                )
                self.tabs.setTabText(i, label)
                break

    # ── Captures ──────────────────────────────────────────────────────

    def _captures_add_row(self) -> None:
        """Append empty capture row to table."""
        r = self.captures_table.rowCount()
        self.captures_table.insertRow(r)

        self.captures_table.setItem(r, 0, QTableWidgetItem(""))

        source_combo = QComboBox()
        source_combo.addItems(_CAPTURE_SOURCES)
        self.captures_table.setCellWidget(r, 1, source_combo)

        self.captures_table.setItem(r, 2, QTableWidgetItem(""))
        self.captures_table.setItem(r, 3, QTableWidgetItem(""))

    def _captures_remove_row(self) -> None:
        """Remove selected capture rows."""
        self._remove_selected_from(self.captures_table)

    def _get_captures(self) -> list[dict[str, str]]:
        """Collect captures from table.

        Returns:
            List of capture dicts with variable/source/path/default
        """
        captures = []
        for r in range(self.captures_table.rowCount()):
            var_item = self.captures_table.item(r, 0)
            variable = var_item.text().strip() if var_item else ""
            if not variable:
                continue

            source_widget = self.captures_table.cellWidget(r, 1)
            source = source_widget.currentText() if source_widget else "json"

            path_item = self.captures_table.item(r, 2)
            path = path_item.text().strip() if path_item else ""

            default_item = self.captures_table.item(r, 3)
            default = default_item.text().strip() if default_item else ""

            captures.append(
                {"variable": variable, "source": source, "path": path, "default": default}
            )

        return captures

    def _set_captures(self, captures: list[dict[str, str]] | None) -> None:
        """Populate captures table from capture list.

        Args:
            captures: List of capture dicts or None
        """
        self.captures_table.setRowCount(0)
        for cap in captures or []:
            if not isinstance(cap, dict):
                continue

            r = self.captures_table.rowCount()
            self.captures_table.insertRow(r)

            self.captures_table.setItem(r, 0, QTableWidgetItem(cap.get("variable", "")))

            source_combo = QComboBox()
            source_combo.addItems(_CAPTURE_SOURCES)
            src = cap.get("source", "json")
            idx = source_combo.findText(src)
            if idx >= 0:
                source_combo.setCurrentIndex(idx)
            self.captures_table.setCellWidget(r, 1, source_combo)

            self.captures_table.setItem(r, 2, QTableWidgetItem(cap.get("path", "")))
            self.captures_table.setItem(r, 3, QTableWidgetItem(cap.get("default", "")))

    # ... existing code ...

    # ── Body type / tab labels ─────────────────────────────────────────

    def _on_body_type_changed(self, _index: int) -> None:
        sel = self.body_type_combo.currentText()
        is_multipart = sel == "multipart/form-data"
        is_json = sel == "raw (JSON)"
        is_gql = sel == "GraphQL"

        # Show/hide the multipart table vs. raw text editor vs. GraphQL editor
        self.body_text.setVisible(not is_multipart and not is_gql)
        self._multipart_table.setVisible(is_multipart)
        self._mp_toolbar.setVisible(is_multipart)
        self._fmt_json_btn.setVisible(is_json)
        self._gql_widget.setVisible(is_gql)

        if sel == "none":
            self.body_text.setEnabled(False)
            self.body_text.setPlaceholderText("(no body)")
        elif not is_multipart and not is_gql:
            self.body_text.setEnabled(True)
            ph = {
                "raw (JSON)": '{\n  "key": "value"\n}',
                "raw (XML)": "<root>\n  <item>value</item>\n</root>",
                "raw (text)": "Plain text body",
                "form-urlencoded": "key1=value1&key2=value2",
            }
            self.body_text.setPlaceholderText(ph.get(sel, ""))

        self._update_tab_labels()

    def _set_tab_text_by_base_label(self, base_label: str, text: str) -> None:
        """Update the first tab whose label matches *base_label* ignoring badges."""
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith(base_label):
                self.tabs.setTabText(i, text)
                return

    @staticmethod
    def _non_empty_table_rows(table: QTableWidget, key_column: int = 0) -> int:
        """Count rows whose key column contains meaningful user data."""
        count = 0
        for row in range(table.rowCount()):
            item = table.item(row, key_column)
            if item and item.text().strip():
                count += 1
        return count

    def _update_tab_labels(self, *_args: Any) -> None:
        """Update tab labels to show data counts as badges."""
        try:
            h = len(self.headers_table.get_data())
            # Show total param count (enabled + disabled) so users see all saved params
            qp = len(self.params_table.get_all_rows())
            pp = self.path_params_table.rowCount()
            total_p = qp + pp
            self._set_tab_text_by_base_label("Headers", f"Headers ({h})" if h else "Headers")
            self._set_tab_text_by_base_label(
                "Params", f"Params ({total_p})" if total_p else "Params"
            )
            bt = self.body_type_combo.currentText()
            if bt == "multipart/form-data":
                mp = len(self._get_multipart_data())
                self._set_tab_text_by_base_label("Body", f"Body ({mp})" if mp else "Body")
            elif bt != "none" and self.body_text.toPlainText().strip():
                self._set_tab_text_by_base_label("Body", "Body ●")
            else:
                self._set_tab_text_by_base_label("Body", "Body")

            captures = self._non_empty_table_rows(self.captures_table)
            self._set_tab_text_by_base_label(
                _LABEL_CAPTURES,
                f"{_LABEL_CAPTURES} ({captures})" if captures else _LABEL_CAPTURES,
            )

            has_scripts = bool(
                self.pre_script_editor.toPlainText().strip()
                or self.post_script_editor.toPlainText().strip()
            )
            self._set_tab_text_by_base_label("Scripts", "Scripts ●" if has_scripts else "Scripts")

            has_notes = bool(self.notes_editor.toPlainText().strip())
            self._set_tab_text_by_base_label("Notes", "Notes ●" if has_notes else "Notes")
        except Exception:
            logger.debug("Failed to update tab labels", exc_info=True)

    # ── Body search utilities ─────────────────────────────────────────

    def _body_editor_target(self) -> tuple[Any, str]:
        """Return ``(editor_widget | None, body_text_str)`` for the body editor.

        Handles the :class:`_BodyTextProxy` indirection used in headless
        environments where the underlying C++ widget may be unavailable.
        """
        has_widget = getattr(self.body_text, "_has_widget", lambda: False)()
        target = getattr(self.body_text, "_widget", None) if has_widget else None
        text = (
            target.toPlainText() if target is not None else getattr(self.body_text, "_buffer", "")
        )
        return target, text

    @property
    def _re_flags(self) -> int:
        """``0`` when case-sensitive search is active, ``re.IGNORECASE`` otherwise."""
        return (
            0
            if (getattr(self, "_body_case_cb", None) and self._body_case_cb.isChecked())
            else re.IGNORECASE
        )

    @staticmethod
    def _make_extra_selection(
        target, start: int, end: int, fmt: QTextCharFormat
    ) -> QTextEdit.ExtraSelection | None:
        """Build a :class:`QTextEdit.ExtraSelection` spanning [*start*, *end*)."""
        # Guard against out-of-range positions which can occur when the
        # underlying document has changed since offsets were computed.
        try:
            doc = target.document()
            max_pos = max(0, doc.characterCount() - 1)
        except Exception:
            return None

        s = max(0, min(start, max_pos))
        e = max(0, min(end, max_pos))
        if s >= e:
            return None

        sel = QTextEdit.ExtraSelection()
        cursor = target.textCursor()
        cursor.setPosition(s)
        cursor.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        sel.cursor = cursor
        sel.format = fmt
        return sel

    def _body_highlight_all(self) -> None:
        """Highlight all search matches in the body editor."""
        try:
            term_input = getattr(self, "_body_search_input", None)
            if term_input is None:
                return
            term = term_input.text()
            target, doc_text = self._body_editor_target()

            # JSONPath: select first matched value rather than bulk-highlighting
            if (
                getattr(self, "_body_jsonpath_cb", None)
                and self._body_jsonpath_cb.isChecked()
                and term
            ):
                positions = self._find_jsonpath_positions(term)
                if positions and target is not None:
                    start, length = positions[0]
                    self._select_range(target, start, start + length)
                return

            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#fff59d"))
            selections = []

            if getattr(self, "_body_regex_cb", None) and self._body_regex_cb.isChecked():
                if not term:
                    if target is not None:
                        target.setExtraSelections([])
                    return
                try:
                    for m in re.finditer(term, doc_text, self._re_flags):
                        if target is not None:
                            sel = self._make_extra_selection(target, m.start(), m.end(), fmt)
                            if sel is not None:
                                selections.append(sel)
                                if len(selections) >= _MAX_HIGHLIGHTS:
                                    break
                except re.error:
                    pass
            elif term:
                case_on = getattr(self, "_body_case_cb", None) and self._body_case_cb.isChecked()
                haystack = doc_text if case_on else doc_text.lower()
                needle = term if case_on else term.lower()
                start = 0
                while (idx := haystack.find(needle, start)) != -1:
                    if target is not None:
                        sel = self._make_extra_selection(target, idx, idx + len(needle), fmt)
                        if sel is not None:
                            selections.append(sel)
                            if len(selections) >= _MAX_HIGHLIGHTS:
                                break
                    start = idx + max(1, len(needle))

            if target is not None:
                target.setExtraSelections(selections[:_MAX_HIGHLIGHTS])
        except RuntimeError:
            logger.debug("Body editor unavailable while highlighting", exc_info=True)
        except Exception:
            logger.debug("Error during body highlight", exc_info=True)

    def _body_navigate(self, *, forward: bool) -> None:
        """Move the body-editor cursor to the next or previous search match.

        Handles plain-text, regex, and JSONPath search modes with wrap-around.
        *forward=True* → next match; *forward=False* → previous match.
        """
        try:
            term_input = getattr(self, "_body_search_input", None)
            if term_input is None:
                return
            term = term_input.text()
            if not term:
                return
            target, doc_text = self._body_editor_target()

            # ── JSONPath ──────────────────────────────────────────────
            # Navigate to the first (forward) or last (backward) match.
            if getattr(self, "_body_jsonpath_cb", None) and self._body_jsonpath_cb.isChecked():
                positions = self._find_jsonpath_positions(term)
                if positions and target is not None:
                    start, length = positions[0] if forward else positions[-1]
                    self._select_range(target, start, start + length)
                return

            # ── Regex ─────────────────────────────────────────────────
            if getattr(self, "_body_regex_cb", None) and self._body_regex_cb.isChecked():
                if not term:
                    return
                if target is None:
                    return
                cur_pos = target.textCursor().position()
                if forward:
                    # search from cursor to end of document, then wrap
                    m = re.search(term, doc_text[cur_pos:], self._re_flags)
                    if m:
                        start, end = cur_pos + m.start(), cur_pos + m.end()
                    else:
                        m = re.search(term, doc_text, self._re_flags)
                        if not m:
                            return
                        start, end = m.start(), m.end()
                else:
                    matches = [
                        m
                        for m in re.finditer(term, doc_text, self._re_flags)
                        if m.start() < cur_pos
                    ]
                    if not matches:
                        matches = list(re.finditer(term, doc_text, self._re_flags))
                    if not matches:
                        return
                    m = matches[-1]
                    start, end = m.start(), m.end()
                self._select_range(target, start, end)
                return

            # ── Plain text via Qt (native wrap-around) ────────────────
            if target is None:
                return
            if forward:
                found = target.find(term)
                if not found:
                    cur = target.textCursor()
                    cur.movePosition(QTextCursor.MoveOperation.Start)
                    target.setTextCursor(cur)
                    target.find(term)
            else:
                found = target.find(term, QTextDocument.FindFlag.FindBackward)
                if not found:
                    cur = target.textCursor()
                    cur.movePosition(QTextCursor.MoveOperation.End)
                    target.setTextCursor(cur)
                    target.find(term, QTextDocument.FindFlag.FindBackward)
        except RuntimeError:
            direction = "next" if forward else "prev"
            logger.debug("Body editor unavailable during find %s", direction, exc_info=True)
        except Exception:
            logger.debug("Error during body navigate", exc_info=True)

    def _body_find_next(self) -> None:
        self._body_navigate(forward=True)

    def _body_find_prev(self) -> None:
        self._body_navigate(forward=False)

    def _find_jsonpath_positions(self, path: str) -> list[tuple[int, int]]:
        """Small JSON-path evaluator supporting dot and bracket navigation.

        Returns a list of ``(start, length)`` pairs — character offsets and
        byte-accurate lengths in the body text — for each matched value.
        Using the length avoids the fixed ``+50`` heuristic that was used by
        callers to guess where the selection should end.
        """
        _, text = self._body_editor_target()
        if not text:
            return []
        try:
            obj = json.loads(text)
        except Exception:
            return []

        # Parse path like a.b[0].c into steps
        steps = []
        i = 0
        while i < len(path):
            if path[i] == ".":
                i += 1
                continue
            if path[i] == "[":
                j = path.find("]", i)
                if j == -1:
                    return []
                idx = path[i + 1 : j]
                if idx.isdigit():
                    steps.append(int(idx))
                else:
                    steps.append(idx.strip("\"'"))
                i = j + 1
            else:
                j = i
                while j < len(path) and path[j] not in ".[":
                    j += 1
                steps.append(path[i:j])
                i = j

        # Iteratively walk the parsed path steps
        current = obj
        for step in steps:
            if isinstance(step, int):
                if isinstance(current, list) and 0 <= step < len(current):
                    current = current[step]
                else:
                    return []
            else:
                if isinstance(current, dict) and step in current:
                    current = current[step]
                else:
                    return []

        # Locate the matched value in the source text
        try:
            txt = json.dumps(current, ensure_ascii=False)
            off = text.find(txt)
            if off < 0:
                return []
            second = text.find(txt, off + 1)
            if second >= 0:
                logger.debug(
                    "JSONPath highlight skipped due to ambiguous value match path=%s",
                    path,
                )
                return []
            if off >= 0:
                return [(off, len(txt))]
        except Exception:
            pass
        return []

    # ── Load / detect / clear ──────────────────────────────────────────

    def load_request(self, request: Request) -> None:
        # Update internal state first so tests and callers can inspect
        # request metadata and auth even if some GUI widgets are unavailable.
        self._auth = getattr(request, "auth", None)
        self.current_request = request

        # Resolve inherited auth when request has no own auth.
        if self._auth is None:
            try:
                self._resolve_inherited_auth()
            except Exception:
                logger.debug("Failed to resolve inherited auth during load_request", exc_info=True)
        else:
            self._inherited_auth = None
            self._inherited_auth_source = None

        # ── Populate UI widgets (best-effort in headless/test envs) ──

        self._try_ui(self.url_input.setText, request.url)

        def _set_method():
            idx = self.method_combo.findText(request.method)
            if idx >= 0:
                self.method_combo.setCurrentIndex(idx)

        self._try_ui(_set_method)

        self._try_ui(self.headers_table.set_data, request.headers or {})

        pl = getattr(request, "params_list", None)
        self._try_ui(self.params_table.set_data, pl if pl else (request.params or {}))

        # Body / multipart
        mp_data = getattr(request, "multipart_data", None)
        if mp_data:

            def _load_mp():
                self._set_multipart_data(mp_data)
                self.body_type_combo.setCurrentText("multipart/form-data")
                self.body_text.clear()

            self._try_ui(_load_mp)
        elif request.body:

            def _load_body():
                self.body_text.setPlainText(request.body)
                self._multipart_table.setRowCount(0)
                detected = self._detect_body_type(request.body, request.headers)
                self.body_type_combo.setCurrentText(detected)

            self._try_ui(_load_body)
        else:

            def _clear_body():
                self.body_text.clear()
                self._multipart_table.setRowCount(0)
                self.body_type_combo.setCurrentText("none")

            self._try_ui(_clear_body)

        self._try_ui(self._update_auth_display, self._auth)
        self._try_ui(self._set_captures, getattr(request, "captures", None) or [])
        self._try_ui(self._set_assertions, getattr(request, "assertions", None) or [])

        def _load_scripts():
            self.pre_script_editor.setPlainText(getattr(request, "pre_script", "") or "")
            self.post_script_editor.setPlainText(getattr(request, "post_script", "") or "")

        self._try_ui(_load_scripts)

        def _load_certs():
            self.cert_path_input.setText(getattr(request, "cert_path", "") or "")
            self.cert_key_input.setText(getattr(request, "cert_key_path", "") or "")

        self._try_ui(_load_certs)

        def _load_settings():
            self.timeout_spin.setValue(
                getattr(request, "timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT
            )
            self.verify_ssl_check.setChecked(bool(getattr(request, "verify_ssl", True)))
            self.follow_redirects_check.setChecked(bool(getattr(request, "follow_redirects", True)))

        self._try_ui(_load_settings)

        def _clear_script_results():
            self.pre_script_result.setText("")
            self.post_script_result.setText("")

        self._try_ui(_clear_script_results)

        self._try_ui(self.notes_editor.setPlainText, getattr(request, "description", "") or "")

        def _load_path_params():
            self.path_params_table.set_data(getattr(request, "path_params", None) or {})
            self.path_params_table.update_from_url(request.url)
            self._path_params_widget.setVisible(self.path_params_table.rowCount() > 0)

        self._try_ui(_load_path_params)

        # Final housekeeping
        def _housekeeping():
            self._clear_dirty()
            self._update_tab_labels()
            self._update_url_suffix()

        self._try_ui(_housekeeping)

    @staticmethod
    def _detect_body_type(body: str, headers: Dict | None = None) -> str:
        """Guess body type from content or Content-Type header.

        Delegates to the pure-logic helper in ``builder`` so the heuristic
        is unit-testable without a display server.
        """
        from equinox.gui.request_panel.builder import detect_body_type

        return detect_body_type(body, headers)

    def clear(self) -> None:
        """Reset all request fields to their defaults.

        Each widget access is guarded so a deleted C++ object in headless/test
        environments does not prevent the rest of the reset from running.
        """
        try:
            if self._worker is not None and self._worker.isRunning():
                self._cancel_request()
        except RuntimeError:
            pass

        def _reset_widgets():
            self.url_input.clear()
            self.method_combo.setCurrentIndex(0)
            self.headers_table.reset()
            self.params_table.reset()
            self.path_params_table.reset()
            self._path_params_widget.setVisible(False)
            self.body_text.clear()
            self._multipart_table.setRowCount(0)
            self._gql_query.clear()
            self._gql_vars.clear()
            self.body_type_combo.setCurrentIndex(0)
            # Auth intentionally kept — user almost always wants to reuse it
            self.captures_table.setRowCount(0)
            self.captures_results_label.setText("—")
            self.assertions_table.setRowCount(0)
            self.assertions_results_label.setText("—")
            # Reset Assertions tab title
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i).startswith("Assertions"):
                    self.tabs.setTabText(i, "Assertions")
                    break
            self.pre_script_editor.clear()
            self.post_script_editor.clear()
            self.pre_script_result.setText("")
            self.post_script_result.setText("")
            self.cert_path_input.clear()
            self.cert_key_input.clear()
            self.timeout_spin.setValue(DEFAULT_TIMEOUT)
            self.verify_ssl_check.setChecked(True)
            self.follow_redirects_check.setChecked(True)
            self.notes_editor.clear()

        self._try_ui(_reset_widgets)

        # _session_vars intentionally kept — persists for request chaining
        self.current_request = None

        def _housekeeping():
            self._clear_dirty()
            self._update_tab_labels()
            self._update_url_suffix()

        self._try_ui(_housekeeping)

    # ── Multipart helpers ───────────────────────────────────────────

    def _multipart_add_row(self) -> None:
        """Insert a new multipart row (Key, Type, Value/FilePath)."""
        tbl = getattr(self, "_multipart_table", None)
        if tbl is None:
            raise RuntimeError("Multipart table unavailable")
        try:
            row = tbl.rowCount()
            tbl.insertRow(row)
            tbl.setItem(row, 0, QTableWidgetItem(""))
            type_combo = QComboBox()
            type_combo.addItems(["text", "file"])
            tbl.setCellWidget(row, 1, type_combo)
            tbl.setItem(row, 2, QTableWidgetItem(""))
        except RuntimeError:
            raise
        except Exception:
            logger.debug("Failed to add multipart row", exc_info=True)

    def _multipart_remove_row(self) -> None:
        """Remove selected multipart rows."""
        tbl = getattr(self, "_multipart_table", None)
        if tbl is None:
            raise RuntimeError("Multipart table unavailable")
        try:
            rows = sorted({idx.row() for idx in tbl.selectedIndexes()}, reverse=True)
            for r in rows:
                tbl.removeRow(r)
        except RuntimeError:
            raise
        except Exception:
            logger.debug("Failed to remove multipart row", exc_info=True)

    def _multipart_browse_file(self) -> None:
        """Open file dialog and set chosen path into the selected row's Value cell."""
        tbl = getattr(self, "_multipart_table", None)
        if tbl is None:
            raise RuntimeError("Multipart table unavailable")
        try:
            sel = tbl.currentRow()
            if sel < 0:
                # nothing selected
                return
            path, _ = QFileDialog.getOpenFileName(
                self, "Select file to upload", "", "All files (*)"
            )
            if path:
                try:
                    selected = validate_selected_path(path, must_exist=True)
                except ValueError as exc:
                    QMessageBox.warning(self, "Invalid File", str(exc))
                    return
                # Ensure there is an item at col 2
                item = tbl.item(sel, 2)
                if item is None:
                    item = QTableWidgetItem(str(selected))
                    tbl.setItem(sel, 2, item)
                else:
                    item.setText(str(selected))
        except RuntimeError:
            raise
        except Exception:
            logger.debug("Failed to browse multipart file", exc_info=True)

    def _get_multipart_data(self) -> List[dict[str, str]]:
        """Return a list of multipart entries as dicts: {key, type, value}.

        Skip rows with empty key.
        """
        tbl = getattr(self, "_multipart_table", None)
        if tbl is None:
            return []
        out = []
        try:
            for r in range(tbl.rowCount()):
                key_item = tbl.item(r, 0)
                if key_item is None or not key_item.text().strip():
                    continue
                key = key_item.text().strip()
                # Type may be a widget
                type_widget = tbl.cellWidget(r, 1)
                if isinstance(type_widget, QComboBox):
                    t = type_widget.currentText()
                else:
                    t_item = tbl.item(r, 1)
                    t = t_item.text() if t_item else "text"
                val_item = tbl.item(r, 2)
                v = val_item.text() if val_item else ""
                out.append({"key": key, "type": t, "value": v})
        except Exception:
            logger.debug("Failed to read multipart data", exc_info=True)
        return out

    def _set_multipart_data(self, data: Optional[List[dict[str, str]]]) -> None:
        """Load multipart rows from a list of dicts {'key','type','value'}.

        Overwrites existing rows.
        """
        tbl = getattr(self, "_multipart_table", None)
        if tbl is None:
            raise RuntimeError("Multipart table unavailable")
        try:
            tbl.setRowCount(0)
            for entry in data or []:
                row = tbl.rowCount()
                tbl.insertRow(row)
                tbl.setItem(row, 0, QTableWidgetItem(str(entry.get("key", ""))))
                type_combo = QComboBox()
                type_combo.addItems(["text", "file"])
                t = entry.get("type", "text")
                idx = type_combo.findText(t)
                if idx >= 0:
                    type_combo.setCurrentIndex(idx)
                tbl.setCellWidget(row, 1, type_combo)
                tbl.setItem(row, 2, QTableWidgetItem(str(entry.get("value", ""))))
        except Exception:
            logger.debug("Failed to set multipart data", exc_info=True)
