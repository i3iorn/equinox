"""Body-related mixin for RequestPanel: captures, assertions, multipart, load/clear."""

import logging
import json
import re
from typing import List, Optional, Dict, Tuple

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QTextEdit,
)
from PyQt6.QtGui import QTextCursor, QTextCharFormat, QColor, QTextDocument

from equinox.gui.theme import get_mono_font
from equinox.core.request import Request
from equinox.core.assertions import evaluate_assertion as _evaluate_assertion
from equinox.gui.workers import DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)

# ── Module-level constants ────────────────────────────────────────────────────

# Ordered tuples used to populate combo boxes — single source of truth.
_CAPTURE_SOURCES: Tuple[str, ...] = ("json", "header", "regex", "status")
_ASSERTION_TYPES: Tuple[str, ...] = (
    "status", "body_contains", "header_value", "jsonpath", "elapsed_lt"
)

# Fallback selection width (chars) used when a JSONPath match length cannot be
# determined (e.g. the value is not literally present in the serialised text).
_JSONPATH_PREVIEW_CHARS: int = 50


class RequestBodyMixin:
    """Methods for captures, assertions, multipart, body-type handling, load, and clear."""

    # ── Shared tab-building helpers ───────────────────────────────────

    @staticmethod
    def _build_action_tab_shell(
        title: str, add_slot, remove_slot
    ) -> tuple:
        """Build the common outer shell for Captures / Assertions tabs.

        Creates ``QWidget → QVBoxLayout`` with a bold-label toolbar
        (``+ Add`` / ``− Remove`` buttons) already wired to *add_slot* and
        *remove_slot*.  Also constructs and styles the shared results label.

        Returns ``(widget, layout, results_label)``; the caller is responsible
        for inserting the data table and appending the caption + results_label
        to *layout*.
        """
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 2, 0, 0)
        toolbar.setSpacing(2)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight: bold;")
        toolbar.addWidget(lbl)
        add_btn = QPushButton("+ Add")
        add_btn.setFixedWidth(64)
        add_btn.clicked.connect(add_slot)
        remove_btn = QPushButton("− Remove")
        remove_btn.setFixedWidth(80)
        remove_btn.clicked.connect(remove_slot)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(remove_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        results_label = QLabel("—")
        results_label.setFont(get_mono_font())
        results_label.setWordWrap(True)
        results_label.setObjectName("mutedLabel")
        return w, layout, results_label

    @staticmethod
    def _remove_selected_from(table: QTableWidget) -> None:
        """Remove every selected row from *table* (highest index first)."""
        for r in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(r)

    # ── Captures tab ──────────────────────────────────────────────────

    def _create_captures_tab(self) -> QWidget:
        w, layout, self.captures_results_label = self._build_action_tab_shell(
            "Captures", self._captures_add_row, self._captures_remove_row
        )
        self.captures_table = QTableWidget(0, 4)
        self.captures_table.setHorizontalHeaderLabels(["Variable", "Source", "Path / Pattern", "Default"])
        hdr = self.captures_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.captures_table.verticalHeader().setVisible(False)
        self.captures_table.setAlternatingRowColors(True)
        self.captures_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.captures_table)
        layout.addWidget(QLabel("Last capture results:"))
        layout.addWidget(self.captures_results_label)
        return w

    # ── Assertions tab ────────────────────────────────────────────────

    def _create_assertions_tab(self) -> QWidget:
        """Assertions tab — define pass/fail rules evaluated after each response."""
        w, layout, self.assertions_results_label = self._build_action_tab_shell(
            "Assertions", self._assertions_add_row, self._assertions_remove_row
        )
        self.assertions_table = QTableWidget(0, 3)
        self.assertions_table.setHorizontalHeaderLabels(["Type", "Field / Path", "Expected"])
        ahdr = self.assertions_table.horizontalHeader()
        ahdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        ahdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        ahdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.assertions_table.horizontalHeader().setDefaultSectionSize(160)
        self.assertions_table.verticalHeader().setVisible(False)
        self.assertions_table.setAlternatingRowColors(True)
        self.assertions_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.assertions_table)
        layout.addWidget(QLabel("Last assertion results:"))
        layout.addWidget(self.assertions_results_label)

        return w

    def _assertions_add_row(self) -> None:
        """Append a new empty assertion row to the table."""
        row = self.assertions_table.rowCount()
        self.assertions_table.insertRow(row)
        type_combo = QComboBox()
        type_combo.addItems(_ASSERTION_TYPES)
        self.assertions_table.setCellWidget(row, 0, type_combo)
        self.assertions_table.setItem(row, 1, QTableWidgetItem(""))
        self.assertions_table.setItem(row, 2, QTableWidgetItem(""))

    def _assertions_remove_row(self) -> None:
        """Remove the currently selected assertion row(s)."""
        self._remove_selected_from(self.assertions_table)

    def _get_assertions(self) -> list:
        """Collect assertion rules from the assertions table."""
        rules = []
        for row in range(self.assertions_table.rowCount()):
            widget = self.assertions_table.cellWidget(row, 0)
            a_type = widget.currentText() if widget else "status"
            f_item = self.assertions_table.item(row, 1)
            e_item = self.assertions_table.item(row, 2)
            field    = f_item.text().strip() if f_item else ""
            expected = e_item.text().strip() if e_item else ""
            if expected:
                rules.append({"type": a_type, "field": field, "expected": expected})
        return rules

    def _set_assertions(self, rules: list) -> None:
        """Populate the assertions table from a list of rule dicts."""
        self.assertions_table.setRowCount(0)
        for rule in (rules or []):
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

    def _evaluate_assertions(self, response) -> None:
        """Run assertion rules against the response and display results."""
        rules = self._get_assertions()
        if not rules:
            self.assertions_results_label.setText("—")
            return
        lines = []
        for rule in rules:
            passed, msg = _evaluate_assertion(rule, response)
            icon = "✓" if passed else "✗"
            lines.append(f"{icon} {msg}")
        self.assertions_results_label.setText("\n".join(lines) if lines else "—")
        # Update the tab title with pass/fail summary
        passed_count = sum(1 for line in lines if line.startswith("✓"))
        total = len(lines)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith("Assertions"):
                label = f"Assertions ({passed_count}/{total})" if lines else "Assertions"
                self.tabs.setTabText(i, label)
                break

    # ── Captures ──────────────────────────────────────────────────────

    def _captures_add_row(self) -> None:
        r = self.captures_table.rowCount()
        self.captures_table.insertRow(r)
        self.captures_table.setItem(r, 0, QTableWidgetItem(""))
        source_combo = QComboBox()
        source_combo.addItems(_CAPTURE_SOURCES)
        self.captures_table.setCellWidget(r, 1, source_combo)
        self.captures_table.setItem(r, 2, QTableWidgetItem(""))
        self.captures_table.setItem(r, 3, QTableWidgetItem(""))

    def _captures_remove_row(self) -> None:
        self._remove_selected_from(self.captures_table)

    def _get_captures(self) -> list:
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
            captures.append({"variable": variable, "source": source, "path": path, "default": default})
        return captures

    def _set_captures(self, captures: list) -> None:
        self.captures_table.setRowCount(0)
        for cap in captures:
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

    # ── Body type / tab labels ─────────────────────────────────────────

    def _on_body_type_changed(self, _index: int) -> None:
        sel = self.body_type_combo.currentText()
        is_multipart = sel == "multipart/form-data"
        is_json = sel == "raw (JSON)"
        is_gql  = sel == "GraphQL"

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
                "raw (JSON)":      '{\n  "key": "value"\n}',
                "raw (XML)":       "<root>\n  <item>value</item>\n</root>",
                "raw (text)":      "Plain text body",
                "form-urlencoded": "key1=value1&key2=value2",
            }
            self.body_text.setPlaceholderText(ph.get(sel, ""))

        self._update_tab_labels()

    def _update_tab_labels(self, *_args) -> None:
        """Update tab labels to show data counts as badges."""
        try:
            h = len(self.headers_table.get_data())
            # Show total param count (enabled + disabled) so users see all saved params
            qp = len(self.params_table.get_all_rows())
            pp = self.path_params_table.rowCount()
            total_p = qp + pp
            self.tabs.setTabText(0, f"Headers ({h})" if h else "Headers")
            self.tabs.setTabText(1, f"Params ({total_p})" if total_p else "Params")
            bt = self.body_type_combo.currentText()
            if bt == "multipart/form-data":
                mp = len(self._get_multipart_data())
                self.tabs.setTabText(2, f"Body ({mp})" if mp else "Body")
            elif bt != "none" and self.body_text.toPlainText().strip():
                self.tabs.setTabText(2, "Body ●")
            else:
                self.tabs.setTabText(2, "Body")
        except Exception:
            logger.debug("Failed to update tab labels", exc_info=True)

    # ── Body search utilities ─────────────────────────────────────────

    def _body_editor_target(self) -> tuple:
        """Return ``(editor_widget | None, body_text_str)`` for the body editor.

        Handles the :class:`_BodyTextProxy` indirection used in headless
        environments where the underlying C++ widget may be unavailable.
        """
        has_widget = getattr(self.body_text, '_has_widget', lambda: False)()
        target = getattr(self.body_text, '_widget', None) if has_widget else None
        text = (
            target.toPlainText() if target is not None
            else getattr(self.body_text, '_buffer', '')
        )
        return target, text

    @property
    def _re_flags(self) -> int:
        """``0`` when case-sensitive search is active, ``re.IGNORECASE`` otherwise."""
        return (
            0
            if (getattr(self, '_body_case_cb', None) and self._body_case_cb.isChecked())
            else re.IGNORECASE
        )

    @staticmethod
    def _make_extra_selection(
        target, start: int, end: int, fmt: QTextCharFormat
    ) -> Optional["QTextEdit.ExtraSelection"]:
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
            term_input = getattr(self, '_body_search_input', None)
            if term_input is None:
                return
            term = term_input.text()
            target, doc_text = self._body_editor_target()

            # JSONPath: select first matched value rather than bulk-highlighting
            if (
                getattr(self, '_body_jsonpath_cb', None)
                and self._body_jsonpath_cb.isChecked()
                and term
            ):
                positions = self._find_jsonpath_positions(term)
                if positions and target is not None:
                    start, length = positions[0]
                    end = min(start + length, len(doc_text))
                    try:
                        doc = target.document()
                        max_pos = max(0, doc.characterCount() - 1)
                        p = max(0, min(start, max_pos))
                        q = max(0, min(end, max_pos))
                        cursor = target.textCursor()
                        cursor.setPosition(p)
                        cursor.setPosition(q, QTextCursor.MoveMode.KeepAnchor)
                        target.setTextCursor(cursor)
                    except Exception:
                        pass
                return

            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#fff59d"))
            selections = []

            if getattr(self, '_body_regex_cb', None) and self._body_regex_cb.isChecked():
                try:
                    for m in re.finditer(term, doc_text, self._re_flags):
                        if target is not None:
                            sel = self._make_extra_selection(target, m.start(), m.end(), fmt)
                            if sel is not None:
                                selections.append(sel)
                except re.error:
                    pass
            elif term:
                case_on = (
                    getattr(self, '_body_case_cb', None)
                    and self._body_case_cb.isChecked()
                )
                haystack = doc_text if case_on else doc_text.lower()
                needle = term if case_on else term.lower()
                start = 0
                while (idx := haystack.find(needle, start)) != -1:
                    if target is not None:
                        sel = self._make_extra_selection(target, idx, idx + len(needle), fmt)
                        if sel is not None:
                            selections.append(sel)
                    start = idx + max(1, len(needle))

            if target is not None:
                target.setExtraSelections(selections)
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
            term_input = getattr(self, '_body_search_input', None)
            if term_input is None:
                return
            term = term_input.text()
            if not term:
                return
            target, doc_text = self._body_editor_target()

            # ── JSONPath ──────────────────────────────────────────────
            # Navigate to the first (forward) or last (backward) match.
            if getattr(self, '_body_jsonpath_cb', None) and self._body_jsonpath_cb.isChecked():
                positions = self._find_jsonpath_positions(term)
                if positions and target is not None:
                    start, length = positions[0] if forward else positions[-1]
                    end = min(start + length, len(doc_text))
                    try:
                        doc = target.document()
                        max_pos = max(0, doc.characterCount() - 1)
                        s = max(0, min(start, max_pos))
                        e = max(0, min(end, max_pos))
                        cursor = target.textCursor()
                        cursor.setPosition(s)
                        cursor.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
                        target.setTextCursor(cursor)
                    except Exception:
                        pass
                return

            # ── Regex ─────────────────────────────────────────────────
            if getattr(self, '_body_regex_cb', None) and self._body_regex_cb.isChecked():
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
                        m for m in re.finditer(term, doc_text, self._re_flags)
                        if m.start() < cur_pos
                    ]
                    if not matches:
                        matches = list(re.finditer(term, doc_text, self._re_flags))
                    if not matches:
                        return
                    m = matches[-1]
                    start, end = m.start(), m.end()
                try:
                    doc = target.document()
                    max_pos = max(0, doc.characterCount() - 1)
                    s = max(0, min(start, max_pos))
                    e = max(0, min(end, max_pos))
                    if s < e:
                        cursor = target.textCursor()
                        cursor.setPosition(s)
                        cursor.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
                        target.setTextCursor(cursor)
                except Exception:
                    pass
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

    def _find_jsonpath_positions(self, path: str) -> List[Tuple[int, int]]:
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
            if path[i] == '.':
                i += 1
                continue
            if path[i] == '[':
                j = path.find(']', i)
                if j == -1:
                    return []
                idx = path[i+1:j]
                if idx.isdigit():
                    steps.append(int(idx))
                else:
                    steps.append(idx.strip('"\''))
                i = j + 1
            else:
                j = i
                while j < len(path) and path[j] not in '.[':
                    j += 1
                steps.append(path[i:j])
                i = j

        matches: List[Tuple[int, int]] = []

        def walk(o, sidx: int) -> None:
            if sidx >= len(steps):
                try:
                    txt = json.dumps(o, ensure_ascii=False)
                    off = text.find(txt)
                    if off >= 0:
                        matches.append((off, len(txt)))
                except Exception:
                    pass
                return
            step = steps[sidx]
            if isinstance(step, int):
                if isinstance(o, list) and 0 <= step < len(o):
                    walk(o[step], sidx + 1)
            else:
                if isinstance(o, dict) and step in o:
                    walk(o[step], sidx + 1)

        walk(obj, 0)
        return matches

    # ── Load / detect / clear ──────────────────────────────────────────

    def load_request(self, request: Request) -> None:
        # Update internal state first so tests and callers can inspect
        # request metadata and auth even if some GUI widgets are unavailable.
        self._auth = getattr(request, 'auth', None)
        self.current_request = request

        # Resolve inherited auth when request has no own auth. Guard in case
        # DB resolution fails in tests.
        if self._auth is None:
            try:
                self._resolve_inherited_auth()
            except Exception:
                logger.debug("Failed to resolve inherited auth during load_request", exc_info=True)
        else:
            self._inherited_auth = None
            self._inherited_auth_source = None

        # Populate UI widgets — each step is best-effort in headless/test envs.
        try:
            self.url_input.setText(request.url)
        except RuntimeError:
            logger.debug("url_input unavailable while loading request", exc_info=True)

        try:
            idx = self.method_combo.findText(request.method)
            if idx >= 0:
                self.method_combo.setCurrentIndex(idx)
        except RuntimeError:
            logger.debug("method_combo unavailable while loading request", exc_info=True)

        try:
            self.headers_table.set_data(request.headers or {})
        except RuntimeError:
            logger.debug("headers_table unavailable while loading request", exc_info=True)

        # Prefer the rich params_list (with enabled flags) when present
        pl = getattr(request, "params_list", None)
        try:
            self.params_table.set_data(pl if pl else (request.params or {}))
        except RuntimeError:
            logger.debug("params_table unavailable while loading request", exc_info=True)

        mp_data = getattr(request, "multipart_data", None)
        if mp_data:
            try:
                self._set_multipart_data(mp_data)
                self.body_type_combo.setCurrentText("multipart/form-data")
                self.body_text.clear()
            except RuntimeError:
                logger.warning("Multipart widgets unavailable while loading multipart data", exc_info=True)
        elif request.body:
            try:
                self.body_text.setPlainText(request.body)
                self._multipart_table.setRowCount(0)
                detected = self._detect_body_type(request.body, request.headers)
                self.body_type_combo.setCurrentText(detected)
            except RuntimeError:
                logger.warning("Body widgets unavailable while setting request body", exc_info=True)
        else:
            try:
                self.body_text.clear()
                self._multipart_table.setRowCount(0)
                self.body_type_combo.setCurrentText("none")
            except RuntimeError:
                logger.warning("Body/multipart widgets unavailable while clearing for empty body", exc_info=True)

        try:
            self._update_auth_display(self._auth)
        except RuntimeError:
            logger.debug("auth display widgets unavailable while loading request", exc_info=True)

        try:
            self._set_captures(getattr(request, "captures", None) or [])
        except RuntimeError:
            logger.debug("captures table unavailable while loading request", exc_info=True)

        try:
            self._set_assertions(getattr(request, "assertions", None) or [])
        except RuntimeError:
            logger.debug("assertions table unavailable while loading request", exc_info=True)

        try:
            self.pre_script_editor.setPlainText(getattr(request, "pre_script", "") or "")
            self.post_script_editor.setPlainText(getattr(request, "post_script", "") or "")
        except RuntimeError:
            logger.debug("script editors unavailable while loading request", exc_info=True)

        try:
            self.cert_path_input.setText(getattr(request, "cert_path", "") or "")
            self.cert_key_input.setText(getattr(request, "cert_key_path", "") or "")
        except RuntimeError:
            logger.debug("cert inputs unavailable while loading request", exc_info=True)

        try:
            self.timeout_spin.setValue(getattr(request, "timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
            self.verify_ssl_check.setChecked(bool(getattr(request, "verify_ssl", True)))
            self.follow_redirects_check.setChecked(bool(getattr(request, "follow_redirects", True)))
        except RuntimeError:
            logger.debug("settings widgets unavailable while loading request", exc_info=True)

        try:
            self.pre_script_result.setText("")
            self.post_script_result.setText("")
        except RuntimeError:
            pass

        try:
            self.notes_editor.setPlainText(getattr(request, "description", "") or "")
        except RuntimeError:
            logger.debug("notes editor unavailable while loading request", exc_info=True)

        try:
            self.path_params_table.set_data(getattr(request, "path_params", None) or {})
            self.path_params_table.update_from_url(request.url)
            self._path_params_widget.setVisible(self.path_params_table.rowCount() > 0)
        except RuntimeError:
            logger.debug("path params widgets unavailable while loading request", exc_info=True)

        # Final housekeeping — best-effort
        try:
            self._clear_dirty()
            self._update_tab_labels()
            self._update_url_suffix()
        except RuntimeError:
            logger.debug("final UI housekeeping skipped due to missing widgets", exc_info=True)

    @staticmethod
    def _detect_body_type(body: str, headers: Optional[Dict] = None) -> str:
        """Guess body type from content or Content-Type header.

        Delegates to the pure-logic helper in ``builder`` so the heuristic
        is unit-testable without a display server.
        """
        from equinox.gui.request_panel.builder import detect_body_type
        return detect_body_type(body, headers)

    def clear(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._cancel_request()
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
        # _session_vars intentionally kept — persists for request chaining
        self.current_request = None
        self._clear_dirty()
        self._update_tab_labels()
        self._update_url_suffix()

    # ── Multipart helpers ───────────────────────────────────────────

    def _multipart_add_row(self) -> None:
        """Insert a new multipart row (Key, Type, Value/FilePath)."""
        tbl = getattr(self, '_multipart_table', None)
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
        tbl = getattr(self, '_multipart_table', None)
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
        tbl = getattr(self, '_multipart_table', None)
        if tbl is None:
            raise RuntimeError("Multipart table unavailable")
        try:
            sel = tbl.currentRow()
            if sel < 0:
                # nothing selected
                return
            path, _ = QFileDialog.getOpenFileName(self, "Select file to upload", "", "All files (*)")
            if path:
                # Ensure there is an item at col 2
                item = tbl.item(sel, 2)
                if item is None:
                    item = QTableWidgetItem(path)
                    tbl.setItem(sel, 2, item)
                else:
                    item.setText(path)
        except RuntimeError:
            raise
        except Exception:
            logger.debug("Failed to browse multipart file", exc_info=True)

    def _get_multipart_data(self) -> list:
        """Return a list of multipart entries as dicts: {key, type, value}.

        Skip rows with empty key.
        """
        tbl = getattr(self, '_multipart_table', None)
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

    def _set_multipart_data(self, data) -> None:
        """Load multipart rows from a list of dicts {'key','type','value'}.

        Overwrites existing rows.
        """
        tbl = getattr(self, '_multipart_table', None)
        if tbl is None:
            raise RuntimeError("Multipart table unavailable")
        try:
            tbl.setRowCount(0)
            for entry in (data or []):
                row = tbl.rowCount()
                tbl.insertRow(row)
                tbl.setItem(row, 0, QTableWidgetItem(str(entry.get('key', ''))))
                type_combo = QComboBox()
                type_combo.addItems(["text", "file"])
                t = entry.get('type', 'text')
                idx = type_combo.findText(t)
                if idx >= 0:
                    type_combo.setCurrentIndex(idx)
                tbl.setCellWidget(row, 1, type_combo)
                tbl.setItem(row, 2, QTableWidgetItem(str(entry.get('value', ''))))
        except Exception:
            logger.debug("Failed to set multipart data", exc_info=True)

