"""
UIÔÇæbuilder mixins for RequestPanel.

Each mixin encapsulates a single area of UI construction.
This keeps QWidget creation out of the main panel class and
supports clean architecture, testability, and readability.
"""
from __future__ import annotations

from typing import Any
from typing import cast

from equinox.gui.request_panel._constants import CANCEL_BTN_WIDTH
from equinox.gui.request_panel._constants import FMT_JSON_BTN_WIDTH
from equinox.gui.request_panel._constants import METHOD_COMBO_WIDTH
from equinox.gui.request_panel._constants import SEND_BTN_WIDTH
from equinox.gui.theme import get_mono_font
from equinox.gui.widgets import JsonBodyEditor
from equinox.gui.widgets import TabToolbar
from equinox.gui.widgets import TextEditorProxy
from equinox.gui.widgets import UrlLineEdit
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

# ---------------------------------------------------------------------------
# 1. ORCHESTRATION MIXIN
# ---------------------------------------------------------------------------


class RequestPanelOrchestrationMixin:
    """HighÔÇælevel layout assembly for the RequestPanel."""

    def build_request_panel_ui(self: Any) -> None:
        """Build the full request panel UI tree."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # URL bar container
        url_container = QWidget()
        url_layout = QVBoxLayout(url_container)
        url_layout.setContentsMargins(6, 6, 6, 0)
        url_layout.addLayout(self.build_url_bar())

        self._url_hint_label = QLabel("")
        self._url_hint_label.setObjectName("mutedLabel")
        self._url_hint_label.setVisible(False)
        url_layout.addWidget(self._url_hint_label)

        layout.addWidget(url_container)

        # Preflight banner
        self._preflight_banner = self._build_preflight_banner()
        layout.addWidget(self._preflight_banner)

        self.url_input.textChanged.connect(self._on_url_changed_for_path_params)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setObjectName("requestTabs")
        self.tabs.addTab(self._build_headers_tab(), "Headers")
        self.tabs.addTab(self._build_params_tab(), "Params")
        self.tabs.addTab(self.build_body_tab(), "Body")
        self.tabs.addTab(self._create_auth_tab(), "Auth")
        self.tabs.addTab(self._create_captures_tab(), "Captures")
        self.tabs.addTab(self._create_assertions_tab(), "Assertions")
        self.tabs.addTab(self._create_scripts_tab(), "Scripts")
        self.tabs.addTab(self._create_settings_tab(), "Settings")
        self.tabs.addTab(self.build_notes_tab(), "Notes")

        self.configure_tab_metadata()

        configure_tab_persistence(
            self.tabs,
            settings_key=getattr(self, "_KEY_ACTIVE_TAB", "request/active_tab"),
            default_tab="Headers",
            settings=self._settings,
        )

        layout.addWidget(self.tabs, 1)

        # Bottom bar
        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(6, 0, 6, 6)
        bottom_layout.addLayout(self._build_bottom_bar())
        layout.addWidget(bottom_container)

        self._sync_editor_state_ui()

    def configure_tab_metadata(self: Any) -> None:
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


# ---------------------------------------------------------------------------
# 2. URL BAR MIXIN
# ---------------------------------------------------------------------------


class URLBarMixin:
    """Builds the method/URL/send/cancel row."""

    url_input: UrlLineEdit
    _url_fix_suggestion: str | None

    def build_url_bar(self: Any) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)

        self.method_combo = QComboBox()
        self.method_combo.setObjectName("requestMethodCombo")
        self.method_combo.setProperty("usage_track_id", "request.method_combo")
        self.method_combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self.method_combo.setFixedWidth(METHOD_COMBO_WIDTH)

        self.url_input = UrlLineEdit()
        self.url_input.setPlaceholderText(
            "https://api.example.com/v1/resource  ┬À  {{VAR}} for variables  ┬À  Ctrl+N = new",
        )
        self.url_input.returnPressed.connect(self._send_request)

        self._url_fix_button = QToolButton()
        self._url_fix_button.setText("Fix URL")
        self._url_fix_button.setToolTip("Apply suggested URL fix")
        self._url_fix_button.clicked.connect(self._apply_url_fix)
        self._url_fix_button.setVisible(False)
        self._url_fix_suggestion = None

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("sendBtn")
        self.send_button.setProperty("usage_track_id", "request.send")
        self.send_button.setMinimumWidth(SEND_BTN_WIDTH)
        self.send_button.setToolTip("Send request (Ctrl+Enter)")
        self.send_button.clicked.connect(self._send_request)
        self.send_button.setDefault(True)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelBtn")
        self.cancel_button.setProperty("usage_track_id", "request.cancel")
        self.cancel_button.setMinimumWidth(CANCEL_BTN_WIDTH)
        self.cancel_button.setToolTip("Cancel the in-flight request")
        self.cancel_button.clicked.connect(self._cancel_request)
        self.cancel_button.setVisible(False)

        row.addWidget(self.method_combo)
        row.addWidget(self.url_input, 1)
        row.addWidget(self._url_fix_button)
        row.addWidget(self.send_button)
        row.addWidget(self.cancel_button)

        return row


# ---------------------------------------------------------------------------
# 3. BODY TAB MIXIN
# ---------------------------------------------------------------------------


class BodyTabMixin:
    """Builds the Body tab container."""

    def build_body_tab(self: Any) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 4, 0, 0)

        layout.addLayout(self.build_body_type_bar())
        layout.addLayout(self.build_body_search_bar())
        self.build_body_editor(layout)
        self.build_multipart_section(layout)
        self.build_graphql_section(layout)

        return widget


# ---------------------------------------------------------------------------
# 4. BODY TYPE BAR MIXIN
# ---------------------------------------------------------------------------


class BodyTypeBarMixin:
    """Builds the body type selector row."""

    def build_body_type_bar(self: Any) -> QHBoxLayout:
        row = QHBoxLayout()

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

        row.addWidget(QLabel("Type:"))
        row.addWidget(self.body_type_combo)

        self._fmt_json_btn = QPushButton("Format JSON")
        self._fmt_json_btn.setMinimumWidth(FMT_JSON_BTN_WIDTH)
        self._fmt_json_btn.setToolTip("Pretty-print the JSON body (Ctrl+Shift+F)")
        self._fmt_json_btn.clicked.connect(self._format_json_body)
        self._fmt_json_btn.setVisible(False)

        row.addWidget(self._fmt_json_btn)
        row.addStretch()

        return row


# ---------------------------------------------------------------------------
# 5. BODY SEARCH BAR MIXIN
# ---------------------------------------------------------------------------


class BodySearchBarMixin:
    """Builds the inline body-search controls."""

    def build_body_search_bar(self: Any) -> QHBoxLayout:
        self._body_search_input = QLineEdit()
        self._body_search_input.setPlaceholderText("Find in body...")
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
        self._body_jsonpath_cb.setToolTip("Interpret search as JSON path")
        self._body_jsonpath_cb.setFixedWidth(36)

        prev_btn = QToolButton()
        prev_btn.setText("<")
        prev_btn.setFixedSize(24, 24)
        prev_btn.setToolTip("Find previous match")
        prev_btn.clicked.connect(self._body_find_prev)

        next_btn = QToolButton()
        next_btn.setText(">")
        next_btn.setFixedSize(24, 24)
        next_btn.setToolTip("Find next match")
        next_btn.clicked.connect(self._body_find_next)

        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(4)
        row.addWidget(QLabel("Find:"))
        row.addWidget(self._body_search_input, 1)
        row.addWidget(self._body_case_cb)
        row.addWidget(self._body_regex_cb)
        row.addWidget(self._body_jsonpath_cb)
        row.addWidget(prev_btn)
        row.addWidget(next_btn)

        return row


# ---------------------------------------------------------------------------
# 6. BODY EDITOR MIXIN
# ---------------------------------------------------------------------------


class BodyEditorMixin:
    """Creates the JSON body editor and proxy."""

    def build_body_editor(self: Any, layout: QVBoxLayout) -> None:
        real_editor = JsonBodyEditor(cast(QWidget, cast(object, self)))
        proxy = TextEditorProxy(self, real_editor)

        layout.addWidget(real_editor, 1)

        self.body_text = proxy
        self.body_text.setPlaceholderText('{ "key": "value" }')
        self.body_text.setFont(get_mono_font())


# ---------------------------------------------------------------------------
# 7. MULTIPART MIXIN
# ---------------------------------------------------------------------------


class MultipartMixin:
    """Builds the multipart form-data section."""

    def build_multipart_section(self: Any, layout: QVBoxLayout) -> None:
        self._mp_toolbar = TabToolbar(
            "",
            include_file_btn=True,
            parent=cast(QWidget, cast(object, self)),
        )
        self._mp_toolbar.add_clicked.connect(self._multipart_add_row)
        self._mp_toolbar.remove_clicked.connect(self._multipart_remove_row)
        self._mp_toolbar.file_browse_clicked.connect(self._multipart_browse_file)
        self._mp_toolbar.setVisible(False)
        layout.addWidget(self._mp_toolbar)

        self._multipart_table = QTableWidget(0, 3)
        self._multipart_table.setHorizontalHeaderLabels(["Key", "Type", "Value / File Path"])

        header = self._multipart_table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            header.setDefaultSectionSize(140)
        v_header = self._multipart_table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        self._multipart_table.setAlternatingRowColors(True)
        self._multipart_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._multipart_table.setVisible(False)

        layout.addWidget(self._multipart_table, 1)


# ---------------------------------------------------------------------------
# 8. GRAPHQL MIXIN
# ---------------------------------------------------------------------------


class GraphQLMixin:
    """Builds the GraphQL query + variables editor."""

    def build_graphql_section(self: Any, layout: QVBoxLayout) -> None:
        self._gql_widget = QWidget()
        self._gql_widget.setVisible(False)
        layout.addWidget(self._gql_widget, 1)

        gql_layout = QVBoxLayout(self._gql_widget)
        gql_layout.setContentsMargins(0, 4, 0, 0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Query group
        query_group = QGroupBox("Query")
        query_layout = QVBoxLayout(query_group)
        query_layout.setContentsMargins(4, 6, 4, 4)

        self._gql_query = QPlainTextEdit()
        self._gql_query.setPlaceholderText("query {\n  users {\n    id\n    name\n  }\n}")
        self._gql_query.setFont(get_mono_font())
        query_layout.addWidget(self._gql_query)

        # Variables group
        vars_group = QGroupBox("Variables (JSON, optional)")
        vars_layout = QVBoxLayout(vars_group)
        vars_layout.setContentsMargins(4, 6, 4, 4)

        self._gql_vars = QPlainTextEdit()
        self._gql_vars.setPlaceholderText('{\n  "id": 1\n}')
        self._gql_vars.setFont(get_mono_font())
        vars_layout.addWidget(self._gql_vars)

        splitter.addWidget(query_group)
        splitter.addWidget(vars_group)
        splitter.setSizes([200, 120])

        gql_layout.addWidget(splitter, 1)


# ---------------------------------------------------------------------------
# 9. NOTES TAB MIXIN
# ---------------------------------------------------------------------------


class NotesTabMixin:
    """Builds the free-form notes tab."""

    def build_notes_tab(self: Any) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 6, 4, 4)

        layout.addWidget(QLabel("Notes / description for this request:"))

        self.notes_editor = QPlainTextEdit()
        self.notes_editor.setPlaceholderText(
            "Add notes, cURL examples, API docs links, or any context about this request",
        )

        layout.addWidget(self.notes_editor, 1)
        return widget
