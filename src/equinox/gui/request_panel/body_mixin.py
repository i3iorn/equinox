"""Body-related mixin for RequestPanel: captures, assertions, multipart, load/clear."""

import logging
from typing import Optional, Dict

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
)

from equinox.gui.theme import get_mono_font
from equinox.core.request import Request
from equinox.core.assertions import evaluate_assertion as _evaluate_assertion
from equinox.gui.workers import DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)


class _RequestBodyMixin:
    """Methods for captures, assertions, multipart, body-type handling, load, and clear."""

    # ── Captures tab ──────────────────────────────────────────────────

    def _create_captures_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 2, 0, 0)
        toolbar.setSpacing(2)
        # Label so control buttons align consistently across tabs
        cap_label = QLabel("Captures")
        cap_label.setStyleSheet("font-weight: bold;")
        toolbar.addWidget(cap_label)

        add_btn = QPushButton("+ Add")
        add_btn.setFixedWidth(64)
        add_btn.clicked.connect(self._captures_add_row)
        remove_btn = QPushButton("− Remove")
        remove_btn.setFixedWidth(80)
        remove_btn.clicked.connect(self._captures_remove_row)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(remove_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

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
        self.captures_results_label = QLabel("—")
        self.captures_results_label.setFont(get_mono_font())
        self.captures_results_label.setWordWrap(True)
        self.captures_results_label.setObjectName("mutedLabel")
        layout.addWidget(self.captures_results_label)

        return w

    # ── Assertions tab ────────────────────────────────────────────────

    def _create_assertions_tab(self) -> QWidget:
        """Assertions tab — define pass/fail rules evaluated after each response."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 2, 0, 0)
        toolbar.setSpacing(2)
        # Label so control buttons align consistently across tabs
        asrt_label = QLabel("Assertions")
        asrt_label.setStyleSheet("font-weight: bold;")
        toolbar.addWidget(asrt_label)

        add_btn = QPushButton("+ Add")
        add_btn.setFixedWidth(64)
        add_btn.clicked.connect(self._assertions_add_row)
        remove_btn = QPushButton("− Remove")
        remove_btn.setFixedWidth(80)
        remove_btn.clicked.connect(self._assertions_remove_row)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(remove_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

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
        self.assertions_results_label = QLabel("—")
        self.assertions_results_label.setFont(get_mono_font())
        self.assertions_results_label.setWordWrap(True)
        self.assertions_results_label.setObjectName("mutedLabel")
        layout.addWidget(self.assertions_results_label)

        return w

    def _assertions_add_row(self) -> None:
        """Append a new empty assertion row to the table."""
        row = self.assertions_table.rowCount()
        self.assertions_table.insertRow(row)
        type_combo = QComboBox()
        type_combo.addItems([
            "status", "body_contains", "header_value", "jsonpath", "elapsed_lt"
        ])
        self.assertions_table.setCellWidget(row, 0, type_combo)
        self.assertions_table.setItem(row, 1, QTableWidgetItem(""))
        self.assertions_table.setItem(row, 2, QTableWidgetItem(""))

    def _assertions_remove_row(self) -> None:
        """Remove the currently selected assertion row(s)."""
        rows = sorted(
            {idx.row() for idx in self.assertions_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self.assertions_table.removeRow(row)

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
        all_pass = True
        for rule in rules:
            passed, msg = _evaluate_assertion(rule, response)
            icon = "✓" if passed else "✗"
            lines.append(f"{icon} {msg}")
            if not passed:
                all_pass = False
        self.assertions_results_label.setText("\n".join(lines) if lines else "—")
        # Update the tab title with pass/fail summary
        passed_count = sum(1 for l in lines if l.startswith("✓"))
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
        source_combo.addItems(["json", "header", "regex", "status"])
        self.captures_table.setCellWidget(r, 1, source_combo)
        self.captures_table.setItem(r, 2, QTableWidgetItem(""))
        self.captures_table.setItem(r, 3, QTableWidgetItem(""))

    def _captures_remove_row(self) -> None:
        rows = sorted(
            {idx.row() for idx in self.captures_table.selectedIndexes()},
            reverse=True,
        )
        for r in rows:
            self.captures_table.removeRow(r)

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
            source_combo.addItems(["json", "header", "regex", "status"])
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
        self._mp_btns_widget.setVisible(is_multipart)
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

    # ── Multipart form-data ────────────────────────────────────────────

    def _multipart_add_row(self) -> None:
        # Follow the captures tab model: add a row and focus the key cell,
        # but don't rely on auto-edit behavior that differs across tables.
        r = self._multipart_table.rowCount()
        self._multipart_table.insertRow(r)
        self._multipart_table.setItem(r, 0, QTableWidgetItem(""))
        type_combo = QComboBox()
        type_combo.addItems(["text", "file"])
        self._multipart_table.setCellWidget(r, 1, type_combo)
        self._multipart_table.setItem(r, 2, QTableWidgetItem(""))
        # Focus the newly added key cell
        self._multipart_table.setCurrentCell(r, 0)
        item = self._multipart_table.item(r, 0)
        if item:
            self._multipart_table.editItem(item)
        self._dirty = True
        self._update_tab_labels()

    def _multipart_remove_row(self) -> None:
        rows = sorted({idx.row() for idx in self._multipart_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self._multipart_table.removeRow(r)
        self._dirty = True
        self._update_tab_labels()

    def _multipart_browse_file(self) -> None:
        row = self._multipart_table.currentRow()
        if row < 0:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Select File", "", "All Files (*)")
        if not path:
            return
        # Set type to "file"
        type_widget = self._multipart_table.cellWidget(row, 1)
        if type_widget:
            type_widget.setCurrentText("file")
        self._multipart_table.setItem(row, 2, QTableWidgetItem(path))
        self._dirty = True
        # If the key cell is empty, set focus to the key for user clarity
        key_item = self._multipart_table.item(row, 0)
        if key_item and not key_item.text().strip():
            self._multipart_table.setCurrentCell(row, 0)
            self._multipart_table.editItem(key_item)

    def _get_multipart_data(self) -> list:
        fields = []
        for r in range(self._multipart_table.rowCount()):
            key_item = self._multipart_table.item(r, 0)
            key = key_item.text().strip() if key_item else ""
            if not key:
                continue
            type_widget = self._multipart_table.cellWidget(r, 1)
            field_type = type_widget.currentText() if type_widget else "text"
            val_item = self._multipart_table.item(r, 2)
            value = val_item.text() if val_item else ""
            fields.append({"key": key, "type": field_type, "value": value})
        return fields

    def _set_multipart_data(self, fields: list) -> None:
        self._multipart_table.setRowCount(0)
        for field in fields:
            r = self._multipart_table.rowCount()
            self._multipart_table.insertRow(r)
            self._multipart_table.setItem(r, 0, QTableWidgetItem(field.get("key", "")))
            type_combo = QComboBox()
            type_combo.addItems(["text", "file"])
            ft = field.get("type", "text")
            type_combo.setCurrentText(ft if ft in ("text", "file") else "text")
            self._multipart_table.setCellWidget(r, 1, type_combo)
            self._multipart_table.setItem(r, 2, QTableWidgetItem(field.get("value", "")))

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
