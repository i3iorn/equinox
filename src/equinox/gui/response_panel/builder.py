"""UI construction mixin for ResponsePanel.

Contains all ``_build_*`` methods that create the widget tree.  Has no
``__init__`` — relies on ``self.*`` attributes set by ``ResponsePanel.__init__``.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget,
    QTableWidget, QPushButton, QHeaderView,
    QLineEdit, QToolButton, QMenu,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut

from equinox.core.codegen import GENERATORS
from equinox.gui.intelligence_panel import IntelligencePanel
from equinox.gui.response_panel.header_table import HeaderTable
from equinox.gui.response_panel.json_tree import JsonTree
from equinox.gui.response_panel.read_only_text import ReadOnlyText
from equinox.gui.response_panel.search_bar import SearchBar
from equinox.gui.theme import Colors, get_mono_font


class ResponseBuilderMixin:
    """Mixin that provides every ``_build_*`` method for the response panel."""

    # ------------------------------------------------------------------
    # Status Bar
    # ------------------------------------------------------------------

    def _build_status_bar(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()

        self.status_label = QLabel("No response yet")
        self.status_label.setStyleSheet(f"font-weight: bold; color: {Colors.FG_MUTED};")

        self.time_label = QLabel("")
        self.time_label.setObjectName("mutedLabel")

        self.size_label = QLabel("")
        self.size_label.setObjectName("mutedLabel")

        copy_btn = QPushButton("Copy Body")
        copy_btn.setFixedWidth(80)
        copy_btn.setToolTip("Copy response body to clipboard")
        copy_btn.clicked.connect(self._copy_body)

        download_btn = QPushButton("Download…")
        download_btn.setFixedWidth(90)
        download_btn.setToolTip("Save response body to a file")
        download_btn.clicked.connect(self._download_body)

        code_btn = self._build_code_button()

        self._wrap_btn = QToolButton()
        self._wrap_btn.setText("Wrap")
        self._wrap_btn.setCheckable(True)
        self._wrap_btn.setChecked(False)
        self._wrap_btn.setToolTip("Toggle line wrapping in response body")
        self._wrap_btn.toggled.connect(self._toggle_word_wrap)

        self._view_btn, self._view_menu = self._build_view_selector()

        diff_btn = QPushButton("Diff…")
        diff_btn.setFixedWidth(56)
        diff_btn.setToolTip("Compare response body with a history entry")
        diff_btn.clicked.connect(self._diff_with_history)

        row.addWidget(self.status_label)
        row.addStretch()
        row.addWidget(self.time_label)
        row.addWidget(QLabel("|"))
        row.addWidget(self.size_label)
        row.addWidget(self._wrap_btn)
        row.addWidget(self._view_btn)
        row.addWidget(diff_btn)
        row.addWidget(copy_btn)
        row.addWidget(download_btn)
        row.addWidget(code_btn)

        layout.addLayout(row)

    def _build_code_button(self) -> QToolButton:
        btn = QToolButton()
        btn.setText("Code…")
        btn.setToolTip("Generate client code for this request")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

        menu = QMenu(btn)
        for fmt in GENERATORS:
            act = menu.addAction(fmt)
            act.triggered.connect(lambda _, f=fmt: self._copy_as_code(f))

        menu.addSeparator()
        view_act = menu.addAction("View…")
        view_act.triggered.connect(self._view_code_dialog)

        btn.setMenu(menu)
        btn.clicked.connect(self._view_code_dialog)
        return btn

    def _build_view_selector(self):
        btn = QToolButton()
        btn.setText("View")
        btn.setToolTip("Switch between Raw and JSON Tree view")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

        menu = QMenu(btn)
        raw_act = menu.addAction("Raw")
        json_act = menu.addAction("JSON Tree")
        raw_act.setCheckable(True)
        json_act.setCheckable(True)
        raw_act.setChecked(True)
        json_act.setChecked(False)

        raw_act.triggered.connect(lambda: self._on_view_selected("raw"))
        json_act.triggered.connect(lambda: self._on_view_selected("json"))
        btn.setMenu(menu)
        btn.clicked.connect(btn.showMenu)

        self._view_raw_act = raw_act
        self._view_json_act = json_act

        return btn, menu

    # ------------------------------------------------------------------
    # Timings Row
    # ------------------------------------------------------------------

    def _build_timings_row(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()

        self._timings_toggle = QToolButton()
        self._timings_toggle.setText("▶ Timings")
        self._timings_toggle.setCheckable(True)
        self._timings_toggle.setVisible(False)
        self._timings_toggle.clicked.connect(self._on_timings_toggled)

        self._timings_label = QLabel()
        self._timings_label.setObjectName("mutedLabel")
        self._timings_label.setVisible(False)

        row.addWidget(self._timings_toggle)
        row.addWidget(self._timings_label)
        row.addStretch()

        layout.addLayout(row)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _build_tabs(self, layout: QVBoxLayout) -> None:
        self.tabs = QTabWidget()

        self._build_body_tab()
        self._build_headers_tab()
        self._build_cookies_tab()
        self._build_json_tab()
        self._build_sent_request_tab()
        self._build_intelligence_tab()

        layout.addWidget(self.tabs, 1)

    # ------------------------------------------------------------------
    # Body Tab
    # ------------------------------------------------------------------

    def _build_body_tab(self) -> None:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # Large-body warning
        self._body_warning = QWidget()
        warn_row = QHBoxLayout(self._body_warning)
        warn_row.setContentsMargins(4, 2, 4, 2)

        self._body_warn_label = QLabel()
        self._body_warn_label.setStyleSheet(f"color: {Colors.AMBER}; font-weight: bold;")

        load_btn = QPushButton("Load Full")
        load_btn.setFixedWidth(100)
        load_btn.clicked.connect(self._load_large_body)

        warn_row.addWidget(self._body_warn_label)
        warn_row.addStretch()
        warn_row.addWidget(load_btn)
        self._body_warning.setVisible(False)
        vbox.addWidget(self._body_warning)

        # Loading indicator
        self._loading_label = QLabel("Loading…")
        self._loading_label.setObjectName("mutedLabel")
        self._loading_label.setVisible(False)
        vbox.addWidget(self._loading_label)

        # Body text
        self.body_text = ReadOnlyText()

        # Search bar
        self._search_bar = SearchBar(self.body_text, container)
        self._search_bar.set_filter_callback(self._on_jsonpath_filter)

        vbox.addWidget(self.body_text, 1)
        vbox.addWidget(self._search_bar)

        self._body_tab_idx = self.tabs.addTab(container, "Body")

        # Ctrl+F shortcut
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._open_search)

        # Escape hides the search bar when it or its children have focus
        esc = QShortcut(QKeySequence("Escape"), self._search_bar)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self._search_bar.hide)

    # ------------------------------------------------------------------
    # Headers Tab
    # ------------------------------------------------------------------

    def _build_headers_tab(self) -> None:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 2, 0, 0)
        vbox.setSpacing(2)

        search_row = QHBoxLayout()
        self._hdrs_search = QLineEdit()
        self._hdrs_search.setPlaceholderText("Filter headers…")
        self._hdrs_search.setFixedHeight(24)
        self._hdrs_search.setClearButtonEnabled(True)
        self._hdrs_search.textChanged.connect(self._on_hdrs_filter_changed)

        self._hdrs_count_label = QLabel("")
        self._hdrs_count_label.setObjectName("mutedLabel")

        search_row.addWidget(self._hdrs_search, 1)
        search_row.addWidget(self._hdrs_count_label)
        vbox.addLayout(search_row)

        self.resp_headers_table = HeaderTable()
        vbox.addWidget(self.resp_headers_table, 1)

        self.tabs.addTab(container, "Headers")

    # ------------------------------------------------------------------
    # Cookies Tab
    # ------------------------------------------------------------------

    def _build_cookies_tab(self) -> None:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 2, 0, 0)

        self._cookies_table = QTableWidget(0, 7)
        self._cookies_table.setHorizontalHeaderLabels(
            ["Name", "Value", "Domain", "Path", "Expires", "Secure", "HttpOnly"]
        )
        hdr = self._cookies_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4, 5, 6):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        self._cookies_table.verticalHeader().setVisible(False)
        self._cookies_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._cookies_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._cookies_table.setAlternatingRowColors(True)

        vbox.addWidget(self._cookies_table, 1)
        self.tabs.addTab(container, "Cookies")

    # ------------------------------------------------------------------
    # JSON Tree Tab
    # ------------------------------------------------------------------

    def _build_json_tab(self) -> None:
        self._json_tree = JsonTree()
        self._json_tab_idx = self.tabs.addTab(self._json_tree, "JSON")

    # ------------------------------------------------------------------
    # Sent Request Tab
    # ------------------------------------------------------------------

    def _build_sent_request_tab(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Request line
        row = QHBoxLayout()
        self.sent_method_label = QLabel("—")
        self.sent_method_label.setStyleSheet(
            f"font-weight: bold; "
            f"background: {Colors.BG_ALT}; padding: 2px 8px; border-radius: 3px;"
        )

        self.sent_url_label = QLabel("—")
        self.sent_url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sent_url_label.setWordWrap(True)
        self.sent_url_label.setFont(get_mono_font())

        copy_curl_btn = QPushButton("Copy as cURL")
        copy_curl_btn.setFixedWidth(110)
        copy_curl_btn.setToolTip("Copy the request as a cURL command")
        copy_curl_btn.clicked.connect(self._copy_as_curl)

        row.addWidget(self.sent_method_label)
        row.addWidget(self.sent_url_label, 1)
        row.addWidget(copy_curl_btn)
        layout.addLayout(row)

        # Headers
        layout.addWidget(QLabel("Request Headers (as sent — includes auth):"))
        self.sent_headers_table = HeaderTable()
        layout.addWidget(self.sent_headers_table, 2)

        # Body
        layout.addWidget(QLabel("Request Body:"))
        self.sent_body_text = ReadOnlyText()
        self.sent_body_text.setMaximumHeight(180)
        layout.addWidget(self.sent_body_text, 1)

        self.tabs.addTab(container, "Sent Request")

    # ------------------------------------------------------------------
    # Intelligence Tab
    # ------------------------------------------------------------------

    def _build_intelligence_tab(self) -> None:
        self.intelligence_panel = IntelligencePanel()
        self.tabs.addTab(self.intelligence_panel, "Intelligence")


