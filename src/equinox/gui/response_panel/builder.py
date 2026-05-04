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
from equinox.gui.theme import get_mono_font

# Layout spacing constants
_STATUS_BAR_SPACING = 0
_TIMINGS_ROW_SPACING = 0
_BODY_TAB_SPACING = 0
_HEADERS_TAB_MARGINS = (0, 2, 0, 0)
_HEADERS_TAB_SPACING = 2
_COOKIES_TAB_MARGINS = (0, 2, 0, 0)
_SENT_REQUEST_TAB_MARGINS = (4, 4, 4, 4)
_SENT_REQUEST_TAB_SPACING = 6

# Button widths
_BTN_WIDTH_SMALL = 56    # "Diff…"
_BTN_WIDTH_MEDIUM = 80   # "Copy Body"
_BTN_WIDTH_LARGE = 90    # "Download…"
_BTN_WIDTH_XLARGE = 110  # "Copy as cURL"

# Widget sizing
_HEADERS_SEARCH_HEIGHT = 24
_SENT_BODY_MAX_HEIGHT = 180
_BODY_WARNING_MARGINS = (4, 2, 4, 2)

# Cookies table column count
_COOKIES_COLUMNS = 7

# Style object names (used for theme-aware styling)
_STYLE_MUTED_LABEL = "mutedLabel"
_BODY_WARNING_SEPARATOR = "|"


# Private helper functions — widget construction patterns
# ─────────────────────────────────────────────────────────


def _make_muted_label(text: str = "") -> QLabel:
    """Create a muted (de-emphasized) label for secondary information."""
    label = QLabel(text)
    label.setObjectName(_STYLE_MUTED_LABEL)
    return label


def _make_button(text: str, width: int, tooltip: str = "", parent=None) -> QPushButton:
    """Create a button with minimum width and optional tooltip."""
    btn = QPushButton(text, parent)
    btn.setMinimumWidth(width)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def _make_container(margins: tuple[int, int, int, int], spacing: int) -> tuple[QWidget, QVBoxLayout]:
    """Create a QWidget with QVBoxLayout (contents margins and spacing pre-set).

    Returns (container, layout) for convenient setup.
    """
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return container, layout


def _make_shortcut(seq: str, parent, callback) -> QShortcut:
    """Create a keyboard shortcut with proper activation connection."""
    shortcut = QShortcut(QKeySequence(seq), parent)
    shortcut.activated.connect(callback)
    return shortcut


class ResponseBuilderMixin:
    """Mixin that provides every ``_build_*`` method for the response panel."""

    # ------------------------------------------------------------------
    # Status Bar
    # ------------------------------------------------------------------

    def _build_status_bar(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()

        self.status_label = QLabel("No response yet")

        self.time_label = _make_muted_label()
        self.size_label = _make_muted_label()

        copy_btn = _make_button("Copy Body", _BTN_WIDTH_MEDIUM, "Copy response body to clipboard")
        copy_btn.clicked.connect(self._copy_body)

        download_btn = _make_button("Download…", _BTN_WIDTH_LARGE, "Save response body to a file")
        download_btn.clicked.connect(self._download_body)

        code_btn = self._build_code_button()

        self._wrap_btn = QToolButton()
        self._wrap_btn.setText("Wrap")
        self._wrap_btn.setCheckable(True)
        self._wrap_btn.setChecked(False)
        self._wrap_btn.setToolTip("Toggle line wrapping in response body")
        self._wrap_btn.toggled.connect(self._toggle_word_wrap)

        self._view_btn, self._view_menu = self._build_view_selector()
        self._readability_btn, self._readability_menu = self._build_readability_selector()

        self._redact_btn = QToolButton()
        self._redact_btn.setText("Redact")
        self._redact_btn.setCheckable(True)
        self._redact_btn.setToolTip("Preview response with sensitive values masked")
        self._redact_btn.toggled.connect(self._on_redaction_toggled)

        diff_btn = _make_button("Diff…", _BTN_WIDTH_SMALL, "Compare response body with a history entry")
        diff_btn.clicked.connect(self._diff_with_history)

        row.addWidget(self.status_label)
        row.addStretch()
        row.addWidget(self.time_label)
        row.addWidget(QLabel(_BODY_WARNING_SEPARATOR))
        row.addWidget(self.size_label)
        row.addWidget(self._wrap_btn)
        row.addWidget(self._redact_btn)
        row.addWidget(self._readability_btn)
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

    def _build_readability_selector(self):
        btn = QToolButton()
        btn.setText("Mode")
        btn.setToolTip("Switch body readability mode")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

        menu = QMenu(btn)
        pretty_act = menu.addAction("Pretty")
        raw_act = menu.addAction("Raw")
        split_act = menu.addAction("Split")
        diff_act = menu.addAction("Diff")
        for action in (pretty_act, raw_act, split_act, diff_act):
            action.setCheckable(True)
        pretty_act.setChecked(True)

        pretty_act.triggered.connect(lambda: self._on_readability_selected("pretty"))
        raw_act.triggered.connect(lambda: self._on_readability_selected("raw"))
        split_act.triggered.connect(lambda: self._on_readability_selected("split"))
        diff_act.triggered.connect(lambda: self._on_readability_selected("diff"))

        btn.setMenu(menu)
        btn.clicked.connect(btn.showMenu)

        self._readability_actions = {
            "pretty": pretty_act,
            "raw": raw_act,
            "split": split_act,
            "diff": diff_act,
        }
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

        self._timings_label = _make_muted_label()
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
        self._build_connection_tab()
        self._build_intelligence_tab()

        layout.addWidget(self.tabs, 1)

    # ------------------------------------------------------------------
    # Body Tab
    # ------------------------------------------------------------------

    def _build_body_tab(self) -> None:
        container, vbox = _make_container((0, 0, 0, 0), _BODY_TAB_SPACING)

        # Large-body warning
        self._body_warning = QWidget()
        warn_row = QHBoxLayout(self._body_warning)
        warn_row.setContentsMargins(*_BODY_WARNING_MARGINS)

        self._body_warn_label = QLabel()

        load_btn = _make_button("Load Full", _BTN_WIDTH_MEDIUM)
        load_btn.clicked.connect(self._load_large_body)

        warn_row.addWidget(self._body_warn_label)
        warn_row.addStretch()
        warn_row.addWidget(load_btn)
        self._body_warning.setVisible(False)
        vbox.addWidget(self._body_warning)

        # Loading indicator
        self._loading_label = _make_muted_label("Loading…")
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

        # Keyboard shortcuts
        _make_shortcut("Ctrl+F", self, self._open_search)

        esc = QShortcut(QKeySequence("Escape"), self._search_bar)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self._search_bar.hide)

    # ------------------------------------------------------------------
    # Headers Tab
    # ------------------------------------------------------------------

    def _build_headers_tab(self) -> None:
        container, vbox = _make_container(_HEADERS_TAB_MARGINS, _HEADERS_TAB_SPACING)

        search_row = QHBoxLayout()
        self._hdrs_search = QLineEdit()
        self._hdrs_search.setPlaceholderText("Filter headers…")
        self._hdrs_search.setFixedHeight(_HEADERS_SEARCH_HEIGHT)
        self._hdrs_search.setClearButtonEnabled(True)
        self._hdrs_search.textChanged.connect(self._on_hdrs_filter_changed)

        self._hdrs_count_label = _make_muted_label()

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
        container, vbox = _make_container(_COOKIES_TAB_MARGINS, 0)

        self._cookies_table = QTableWidget(0, _COOKIES_COLUMNS)
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
        container, layout = _make_container(_SENT_REQUEST_TAB_MARGINS, _SENT_REQUEST_TAB_SPACING)

        # Request line
        row = QHBoxLayout()
        self.sent_method_label = QLabel("—")

        self.sent_url_label = QLabel("—")
        self.sent_url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sent_url_label.setWordWrap(True)
        self.sent_url_label.setFont(get_mono_font())

        copy_curl_btn = _make_button(
            "Copy as cURL", _BTN_WIDTH_XLARGE, "Copy the request as a cURL command"
        )
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
        self.sent_body_text.setMaximumHeight(_SENT_BODY_MAX_HEIGHT)
        layout.addWidget(self.sent_body_text, 1)

        self.tabs.addTab(container, "Sent Request")

    # ------------------------------------------------------------------
    # Connection Tab
    # ------------------------------------------------------------------

    def _build_connection_tab(self) -> None:
        container, layout = _make_container(_SENT_REQUEST_TAB_MARGINS, _SENT_REQUEST_TAB_SPACING)
        layout.addWidget(QLabel("Connection & TLS details:"))
        self.connection_text = ReadOnlyText()
        layout.addWidget(self.connection_text, 1)
        self.tabs.addTab(container, "Connection")

    # ------------------------------------------------------------------
    # Intelligence Tab
    # ------------------------------------------------------------------

    def _build_intelligence_tab(self) -> None:
        self.intelligence_panel = IntelligencePanel()
        self.tabs.addTab(self.intelligence_panel, "Intelligence")


