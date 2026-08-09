"""RequestPanel layout and UI-construction mixin."""

# mypy: disable-error-code="attr-defined"
from __future__ import annotations

import json
import logging
import time
from typing import Any
from typing import cast
from typing import NamedTuple

from equinox.gui.request_panel._constants import _HEADER_PRESETS
from equinox.gui.request_panel._constants import _KEY_ACTIVE_TAB
from equinox.gui.request_panel._constants import _POLICY_BALANCED
from equinox.gui.request_panel._constants import _POLICY_PERMISSIVE
from equinox.gui.request_panel._constants import _POLICY_STRICT
from equinox.gui.request_panel._constants import _SCRIPTS_CHEAT_TEXT
from equinox.gui.request_panel._constants import BROWSE_BTN_WIDTH
from equinox.gui.request_panel._constants import FMT_JSON_BTN_WIDTH
from equinox.gui.theme import get_mono_font
from equinox.gui.widgets import CheckableKeyValueTable
from equinox.gui.widgets import JsonBodyEditor
from equinox.gui.widgets import PathParamsTable
from equinox.gui.widgets import TabToolbar
from equinox.gui.widgets import TextEditorProxy
from equinox.gui.workers import DEFAULT_TIMEOUT
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QGroupBox
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QHeaderView
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QPlainTextEdit
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QSplitter
from PyQt6.QtWidgets import QTableWidget
from PyQt6.QtWidgets import QTabWidget
from PyQt6.QtWidgets import QToolButton
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

from ...ui_common import configure_tab_persistence
from .scripts_tab_builder import create_scripts_tab

logger = logging.getLogger(__name__)


class _KvTabResult(NamedTuple):
    """Return type for ``_build_kv_tab`` — avoids anonymous 4-tuples."""

    widget: QWidget
    layout: QVBoxLayout
    toolbar: TabToolbar
    table: CheckableKeyValueTable


class RequestPanelLayoutMixin:
    """Layout and UI-construction helpers for RequestPanel."""

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)  # type: ignore[call-overload]
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        url_container = QWidget()
        url_layout = QVBoxLayout(url_container)
        url_layout.setContentsMargins(6, 6, 6, 0)
        url_layout.addLayout(self.build_url_bar())
        self._url_hint_label = QLabel("")
        self._url_hint_label.setObjectName("mutedLabel")
        self._url_hint_label.setVisible(False)
        url_layout.addWidget(self._url_hint_label)
        layout.addWidget(url_container)

        self._preflight_banner = self._build_preflight_banner()
        layout.addWidget(self._preflight_banner)

        self.url_input.textChanged.connect(self._on_url_changed_for_path_params)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("requestTabs")
        self.tabs.addTab(self._build_headers_tab(), "Headers")
        self.tabs.addTab(self._build_params_tab(), "Params")
        self.tabs.addTab(self._build_body_tab(), "Body")
        self.tabs.addTab(self._create_auth_tab(), "Auth")
        self.tabs.addTab(self._create_captures_tab(), "Captures")
        self.tabs.addTab(self._create_assertions_tab(), "Assertions")
        self.tabs.addTab(self._create_scripts_tab(), "Scripts")
        self.tabs.addTab(self._create_settings_tab(), "Settings")
        self.tabs.addTab(self._build_notes_tab(), "Notes")
        self._configure_tab_metadata()
        configure_tab_persistence(
            self.tabs,
            settings_key=_KEY_ACTIVE_TAB,
            default_tab="Headers",
            settings=self._settings,
        )
        layout.addWidget(self.tabs, 1)

        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(6, 0, 6, 6)
        bottom_layout.addLayout(self._build_bottom_bar())
        layout.addWidget(bottom_container)
        self._sync_editor_state_ui()

    def _configure_tab_metadata(self) -> None:
        """Attach stable tooltips to request tabs for faster discovery."""
        tab_tooltips = {
            "Headers": "Request headers sent with the call",
            "Params": "Query-string and path parameters",
            "Body": "Request payload, multipart form data, or GraphQL body",
            "Auth": "Per-request authentication configuration",
            "Captures": "Extract response values into session variables",
            "Assertions": "Verify status, headers, body, and timing rules",
            "Scripts": "Pre-request and post-response Python scripts",
            "Settings": "Timeouts, TLS, redirects, and client certificate options",
            "Notes": "Request documentation, examples, and working notes",
        }
        for index in range(self.tabs.count()):
            label = self.tabs.tabText(index)
            tooltip = tab_tooltips.get(label)
            if tooltip:
                self.tabs.setTabToolTip(index, tooltip)

    def _set_url_validation_hint(self, message: str, is_error: bool = False) -> None:
        """Show a small inline hint below the URL field."""
        msg = (message or "").strip()
        self._url_hint_label.setVisible(bool(msg))
        self._url_hint_label.setText(msg)
        self._url_hint_label.setObjectName("field-error" if is_error else "mutedLabel")

    def _set_url_fix_suggestion(self, suggestion: str | None, reason: str = "") -> None:
        """Expose a one-click URL fix when validation can safely auto-correct."""
        self._url_fix_suggestion = suggestion
        can_fix = bool(suggestion)
        self._url_fix_button.setVisible(can_fix)
        if can_fix:
            self._url_fix_button.setToolTip(reason or "Apply suggested URL fix")

    def _apply_url_fix(self) -> None:
        """Apply the pending URL fix suggestion, if available."""
        if not self._url_fix_suggestion:
            return
        self.url_input.setText(self._url_fix_suggestion)
        self._set_url_fix_suggestion(None)
        self._set_url_validation_hint("URL auto-correct applied.", is_error=False)

    def _build_bottom_bar(self) -> QHBoxLayout:
        """Compatibility wrapper for the shared bottom-bar builder mixin."""
        return cast(QHBoxLayout, self.build_bottom_bar())

    def _build_preflight_banner(self) -> QWidget:
        banner = QWidget()
        banner.setObjectName("preflightBanner")
        pf_row = QHBoxLayout(banner)
        pf_row.setContentsMargins(6, 2, 4, 2)
        pf_row.setSpacing(6)
        self._preflight_label = QLabel("")
        self._preflight_label.setWordWrap(True)
        pf_dismiss = QToolButton()
        pf_dismiss.setText("✕")
        pf_dismiss.setFixedSize(20, 20)
        pf_dismiss.clicked.connect(lambda: banner.setVisible(False))
        pf_row.addWidget(self._preflight_label, 1)
        pf_row.addWidget(pf_dismiss)
        banner.setVisible(False)
        return banner

    def _build_kv_tab(
        self,
        title: str,
        *,
        presets: list[tuple[str, str, str] | None] | None = None,
        enable_key_completer: bool = False,
    ) -> _KvTabResult:
        """Shared boilerplate for Headers / Params tabs."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)
        if presets:
            toolbar = TabToolbar(
                "",
                presets=presets,
                preset_context=f"request_{title}",
                parent=cast(QWidget, self),
            )
        else:
            toolbar = TabToolbar("", presets=presets, parent=cast(QWidget, self))
        table = CheckableKeyValueTable(enable_key_completer=enable_key_completer)
        toolbar.add_clicked.connect(lambda: self._add_row_and_focus(table))
        toolbar.remove_clicked.connect(lambda: self._remove_table_rows(table))
        toolbar.enable_all_clicked.connect(lambda: self._set_all_checkable(table, True))
        toolbar.disable_all_clicked.connect(lambda: self._set_all_checkable(table, False))
        layout.addWidget(toolbar)
        layout.addWidget(table, 1)
        return _KvTabResult(w, layout, toolbar, table)

    def _build_headers_tab(self) -> QWidget:
        result = self._build_kv_tab("Headers", presets=_HEADER_PRESETS, enable_key_completer=True)
        self.headers_table = result.table
        self._headers_toolbar = result.toolbar
        result.toolbar.preset_selected.connect(self._insert_header_preset)
        return result.widget

    def _build_params_tab(self) -> QWidget:
        result = self._build_kv_tab("Query Parameters")
        self.params_table = result.table
        self._path_params_widget = QWidget()
        pp_inner = QVBoxLayout(self._path_params_widget)
        pp_inner.setContentsMargins(0, 6, 0, 0)
        pp_inner.setSpacing(2)
        pp_label = QLabel("Path Parameters")
        pp_inner.addWidget(pp_label)
        self.path_params_table = PathParamsTable()
        pp_inner.addWidget(self.path_params_table)
        self._path_params_widget.setVisible(False)
        result.layout.addWidget(self._path_params_widget, 1)
        return result.widget

    def _build_body_tab(self) -> QWidget:
        """Body tab: type selector, inline search, raw editor, multipart, and GraphQL."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 4, 0, 0)

        layout.addLayout(self._build_body_type_bar())
        layout.addLayout(self._build_body_search_bar())
        self._build_body_editor(layout)
        self._build_multipart_section(layout)
        self._build_graphql_section(layout)

        return w

    def _build_body_type_bar(self) -> QHBoxLayout:
        """Body-type combo and format button."""
        type_bar = QHBoxLayout()
        type_bar.setContentsMargins(10, 0, 0, 0)
        self.body_type_combo = QComboBox()
        self.body_type_combo.addItems(
            [
                "none",
                "raw (JSON)",
                "raw (XML)",
                "raw (text)",
                "form-urlencoded",
                "multipart/form-data",
                "GraphQL",
            ],
        )
        self.body_type_combo.currentIndexChanged.connect(self._on_body_type_changed)
        type_bar.addWidget(QLabel("Type:"))
        type_bar.addWidget(self.body_type_combo)
        self._fmt_json_btn = QPushButton("Format JSON")
        self._fmt_json_btn.setMinimumWidth(FMT_JSON_BTN_WIDTH)
        self._fmt_json_btn.setToolTip("Pretty-print the JSON body (Ctrl+Shift+F)")
        self._fmt_json_btn.clicked.connect(self._format_json_body)
        self._fmt_json_btn.setVisible(False)
        type_bar.addWidget(self._fmt_json_btn)
        type_bar.addStretch()
        return type_bar

    def _build_body_search_bar(self) -> QHBoxLayout:
        """Inline find bar for the body editor."""
        self._body_search_input = QLineEdit()
        self._body_search_input.setPlaceholderText("Find in body…")
        self._body_search_input.setFixedHeight(26)
        self._body_search_input.setClearButtonEnabled(True)

        self._body_search_input.returnPressed.connect(self._body_find_next)
        self._body_search_input.textChanged.connect(self._body_highlight_all)

        self._body_case_cb = QCheckBox("Aa")
        self._body_case_cb.setToolTip("Case-sensitive")
        self._body_case_cb.setFixedWidth(36)
        self._body_regex_cb = QCheckBox(".*")
        self._body_regex_cb.setToolTip("Use regular expression")
        self._body_regex_cb.setFixedWidth(36)
        self._body_jsonpath_cb = QCheckBox("$.")
        self._body_jsonpath_cb.setToolTip("Interpret search as JSON path (dot/bracket syntax)")
        self._body_jsonpath_cb.setFixedWidth(36)

        prev_btn = QToolButton()
        prev_btn.setText("▲")
        prev_btn.setFixedSize(24, 24)
        prev_btn.setToolTip("Find previous match")
        next_btn = QToolButton()
        next_btn.setText("▼")
        next_btn.setFixedSize(24, 24)
        next_btn.setToolTip("Find next match")

        prev_btn.clicked.connect(self._body_find_prev)
        next_btn.clicked.connect(self._body_find_next)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 2, 0, 0)
        search_row.setSpacing(4)
        search_row.addWidget(QLabel("Find:"))
        search_row.addWidget(self._body_search_input, 1)
        search_row.addWidget(self._body_case_cb)
        search_row.addWidget(self._body_regex_cb)
        search_row.addWidget(self._body_jsonpath_cb)
        search_row.addWidget(prev_btn)
        search_row.addWidget(next_btn)
        return search_row

    def _build_body_editor(self, layout: QVBoxLayout) -> None:
        """Create the raw/text body editor and wrap it in a resilient proxy."""
        real_body = JsonBodyEditor(cast(QWidget, self))
        proxy = TextEditorProxy(self, real_body)
        layout.addWidget(real_body, 1)
        self.body_text = proxy
        self.body_text.setPlaceholderText('{ "key": "value" }')
        self.body_text.setFont(get_mono_font())

    def _build_multipart_section(self, layout: QVBoxLayout) -> None:
        """Multipart form-data toolbar and table."""
        self._mp_toolbar = TabToolbar("", include_file_btn=True, parent=cast(QWidget, self))
        self._mp_toolbar.add_clicked.connect(self._multipart_add_row)
        self._mp_toolbar.remove_clicked.connect(self._multipart_remove_row)
        self._mp_toolbar.file_browse_clicked.connect(self._multipart_browse_file)
        self._mp_toolbar.setVisible(False)
        layout.addWidget(self._mp_toolbar)

        self._multipart_table = QTableWidget(0, 3)
        self._multipart_table.setHorizontalHeaderLabels(["Key", "Type", "Value / File Path"])
        _mp_hdr = self._multipart_table.horizontalHeader()
        if _mp_hdr is not None:
            _mp_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            _mp_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            _mp_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            _mp_hdr.setDefaultSectionSize(140)
        _mp_vhdr = self._multipart_table.verticalHeader()
        if _mp_vhdr is not None:
            _mp_vhdr.setVisible(False)
        self._multipart_table.setAlternatingRowColors(True)
        self._multipart_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._multipart_table.setVisible(False)
        layout.addWidget(self._multipart_table, 1)

    def _build_graphql_section(self, layout: QVBoxLayout) -> None:
        """GraphQL query + variables split editor."""
        self._gql_widget = QWidget()
        self._gql_widget.setVisible(False)
        layout.addWidget(self._gql_widget, 1)
        gql_layout = QVBoxLayout(self._gql_widget)
        gql_layout.setContentsMargins(0, 4, 0, 0)
        gql_splitter = QSplitter(Qt.Orientation.Vertical)
        q_group = QGroupBox("Query")
        q_lay = QVBoxLayout(q_group)
        q_lay.setContentsMargins(4, 6, 4, 4)
        self._gql_query = QPlainTextEdit()
        self._gql_query.setPlaceholderText("query {\n  users {\n    id\n    name\n  }\n}")
        self._gql_query.setFont(get_mono_font())
        q_lay.addWidget(self._gql_query)
        v_group = QGroupBox("Variables (JSON, optional)")
        v_lay = QVBoxLayout(v_group)
        v_lay.setContentsMargins(4, 6, 4, 4)
        self._gql_vars = QPlainTextEdit()
        self._gql_vars.setPlaceholderText('{\n  "id": 1\n}')
        self._gql_vars.setFont(get_mono_font())
        v_lay.addWidget(self._gql_vars)
        gql_splitter.addWidget(q_group)
        gql_splitter.addWidget(v_group)
        gql_splitter.setSizes([200, 120])
        gql_layout.addWidget(gql_splitter, 1)

    def _build_notes_tab(self) -> QWidget:
        """Notes tab: free-form description for the request."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 6, 4, 4)
        layout.addWidget(QLabel("Notes / description for this request:"))
        self.notes_editor = QPlainTextEdit()
        self.notes_editor.setPlaceholderText(
            "Add notes, cURL examples, API docs links, or any context about this request…",
        )
        layout.addWidget(self.notes_editor, 1)
        return w

    def _create_scripts_tab(self) -> QWidget:
        """Compatibility wrapper for the shared scripts-tab builder."""
        return create_scripts_tab(self, _SCRIPTS_CHEAT_TEXT)

    def _create_settings_tab(self) -> QWidget:
        """Compatibility wrapper for the shared settings-tab builder mixin."""
        return cast(
            QWidget,
            self.create_settings_tab(
                default_timeout=DEFAULT_TIMEOUT,
                browse_button_width=BROWSE_BTN_WIDTH,
                policy_options=(_POLICY_STRICT, _POLICY_BALANCED, _POLICY_PERMISSIVE),
            ),
        )

    def _on_policy_profile_changed(self, profile: str) -> None:
        """Apply and persist guardrail profile selection."""
        profile = str(profile or _POLICY_BALANCED)
        self._policy_profile = profile
        try:
            self._settings.setValue("request/policy_profile", profile)
        except Exception:
            logger.exception("Failed to persist policy profile", exc_info=True)

        if profile == _POLICY_STRICT:
            self.verify_ssl_check.setChecked(True)
            self.follow_redirects_check.setChecked(False)
            self._policy_hint.setText(
                "Strict: blocks insecure HTTP, enforces SSL verification, disables scripts, and warns on redirects.",
            )
        elif profile == _POLICY_PERMISSIVE:
            self._policy_hint.setText(
                "Permissive: allows advanced flows with fewer preflight guardrails. Use for trusted test environments only.",
            )
        else:
            self.verify_ssl_check.setChecked(True)
            self.follow_redirects_check.setChecked(True)
            self._policy_hint.setText(
                "Balanced: secure defaults with practical flexibility for day-to-day API testing.",
            )

    def get_policy_profile(self) -> str:
        """Return currently selected request-policy profile."""
        return str(getattr(self, "_policy_profile", _POLICY_BALANCED))

    def _update_url_suffix(self, *_: Any) -> None:
        """Repaint the URL bar with the current enabled params as a ghost suffix."""
        try:
            enabled = self.params_table.get_enabled_data()
            if not enabled:
                self.url_input.set_param_suffix("")
                return
            sep = "&" if "?" in self.url_input.text() else "?"
            parts = [f"{k}={v}" for k, v in enabled.items() if k]
            self.url_input.set_param_suffix(sep + "&".join(parts))
        except Exception:
            logger.exception("Failed to update URL suffix", exc_info=True)
            self.url_input.set_param_suffix("")

    def _on_url_changed_for_path_params(self, text: str) -> None:
        """Show/hide path-params section within the Params tab."""
        try:
            self.path_params_table.update_from_url(text)
            visible = self.path_params_table.rowCount() > 0
            self._path_params_widget.setVisible(visible)
            self._update_tab_labels()
        except Exception:
            logger.exception("Failed to update path parameters from URL", exc_info=True)

    def _format_json_body(self) -> None:
        """Pretty-print the JSON in the body editor."""
        text = self.body_text.toPlainText()
        if not text.strip():
            return
        t0 = time.perf_counter()
        try:
            formatted = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            self.body_text.setPlainText(formatted)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "request_panel.json_formatted op=format_json_body original_length=%d formatted_length=%d elapsed_ms=%d",
                len(text),
                len(formatted),
                elapsed_ms,
            )
        except json.JSONDecodeError as exc:
            logger.warning(
                "JSON formatting failed: %s (line %d, col %d)",
                exc.msg,
                exc.lineno,
                exc.colno,
            )
            self._status_message(f"Invalid JSON: {exc}")
