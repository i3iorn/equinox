"""Main window for Equinox GUI"""

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QTabWidget,
    QMenuBar,
    QMenu,
    QStatusBar,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction

from equinox.storage import Database
from equinox.gui.request_panel import RequestPanel
from equinox.gui.response_panel import ResponsePanel
from equinox.gui.collections_panel import CollectionsPanel
from equinox.gui.history_panel import HistoryPanel


class MainWindow(QMainWindow):
    """Main application window"""

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setWindowTitle("Equinox - API Testing Tool")
        self.setGeometry(100, 100, 1400, 900)

        self._init_ui()
        self._create_menu_bar()
        self._create_status_bar()

    def _init_ui(self):
        """Initialize UI components"""
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main layout
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Create main splitter
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left sidebar with tabs
        left_tabs = QTabWidget()
        self.collections_panel = CollectionsPanel(self.db, self)
        self.history_panel = HistoryPanel(self.db, self)
        left_tabs.addTab(self.collections_panel, "Collections")
        left_tabs.addTab(self.history_panel, "History")
        left_tabs.setMaximumWidth(400)

        # Right side - request and response
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Request/Response splitter
        req_resp_splitter = QSplitter(Qt.Orientation.Vertical)

        self.request_panel = RequestPanel(self.db, self)
        self.response_panel = ResponsePanel(self)

        req_resp_splitter.addWidget(self.request_panel)
        req_resp_splitter.addWidget(self.response_panel)
        req_resp_splitter.setSizes([400, 500])

        right_layout.addWidget(req_resp_splitter)

        # Add to main splitter
        main_splitter.addWidget(left_tabs)
        main_splitter.addWidget(right_widget)
        main_splitter.setSizes([300, 1100])

        main_layout.addWidget(main_splitter)

        # Connect signals
        self.request_panel.response_received.connect(self.response_panel.display_response)
        self.collections_panel.request_selected.connect(self.request_panel.load_request)
        self.history_panel.history_selected.connect(self._load_history_entry)

    def _create_menu_bar(self):
        """Create menu bar"""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        new_request_action = QAction("&New Request", self)
        new_request_action.setShortcut("Ctrl+N")
        new_request_action.triggered.connect(self.request_panel.clear)
        file_menu.addAction(new_request_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Collections menu
        collections_menu = menubar.addMenu("&Collections")

        new_collection_action = QAction("New &Collection", self)
        new_collection_action.triggered.connect(self.collections_panel.create_collection)
        collections_menu.addAction(new_collection_action)

        refresh_action = QAction("&Refresh", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self.collections_panel.refresh)
        collections_menu.addAction(refresh_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _create_status_bar(self):
        """Create status bar"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _load_history_entry(self, history_id: int):
        """Load history entry into request panel"""
        from equinox.storage import HistoryManager

        mgr = HistoryManager(self.db)
        entry = mgr.get_history(history_id)

        if entry:
            # Load request details
            from equinox.core.request import Request

            request = Request(
                method=entry["method"],
                url=entry["url"],
                headers=entry["request_headers"],
                body=entry["request_body"],
            )
            self.request_panel.load_request(request)

            # If response available, show it
            if entry.get("status_code"):
                from equinox.core.request import Response
                from datetime import datetime

                response = Response(
                    status_code=entry["status_code"],
                    reason=entry["reason"] or "",
                    headers=entry["response_headers"] or {},
                    body=entry["response_body"] or b"",
                    elapsed=entry["elapsed"] or 0.0,
                    request=request,
                    timestamp=datetime.fromisoformat(entry["executed_at"]),
                )
                self.response_panel.display_response(response)

    def _show_about(self):
        """Show about dialog"""
        from PyQt6.QtWidgets import QMessageBox
        from equinox import __version__

        QMessageBox.about(
            self,
            "About Equinox",
            f"<h2>Equinox v{__version__}</h2>"
            "<p>A local-first API testing tool</p>"
            "<p>Built with Python and PyQt6</p>",
        )
