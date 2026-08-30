"""Coverage-boosting tests for GUI panels."""

from unittest.mock import MagicMock

from PyQt6.QtWidgets import QLabel
from .gui_helpers import process as _process


# ─────────────────────────────────────────────────────────────────────────────
# LoggingPanel
# ─────────────────────────────────────────────────────────────────────────────


class TestLoggingPanel:
    def _make_panel(self):
        from equinox.gui.logging_panel import LoggingPanel

        return LoggingPanel()

    def test_instantiate(self):
        p = self._make_panel()
        assert p is not None
        assert hasattr(p, "list_widget")
        assert hasattr(p, "detail_text")

    def test_log_request(self):
        p = self._make_panel()
        req = MagicMock()
        req.method = "GET"
        req.url = "https://example.com/api"
        req.headers = {"Accept": "application/json"}
        req.params = {}
        req.body = None
        p.log_request(req)
        _process()
        assert len(p._entries) == 1
        assert p._entries[0]["type"] == "request"

    def test_log_response(self):
        p = self._make_panel()
        req = MagicMock()
        req.method = "POST"
        req.url = "https://example.com/api"
        resp = MagicMock()
        resp.status_code = 200
        resp.reason = "OK"
        resp.elapsed = 0.123
        resp.size = 512
        resp.headers = {"Content-Type": "application/json"}
        p.log_response(req, resp)
        _process()
        assert len(p._entries) == 1
        assert p._entries[0]["type"] == "response"
        assert p._entries[0]["status"] == 200

    def test_log_error_response(self):
        p = self._make_panel()
        req = MagicMock()
        req.method = "GET"
        req.url = "https://example.com/api"
        resp = MagicMock()
        resp.status_code = 500
        resp.reason = "Internal Server Error"
        resp.elapsed = 0.05
        resp.size = 100
        resp.headers = {}
        p.log_response(req, resp)
        _process()
        assert p._entries[0]["status"] == 500

    def test_log_error(self):
        p = self._make_panel()
        req = MagicMock()
        req.method = "GET"
        req.url = "https://example.com/api"
        p.log_error(req, "Connection refused")
        _process()
        assert len(p._entries) == 1
        assert p._entries[0]["type"] == "error"

    def test_clear(self):
        p = self._make_panel()
        req = MagicMock()
        req.method = "GET"
        req.url = "https://example.com"
        req.headers = {}
        req.params = {}
        req.body = None
        p.log_request(req)
        _process()
        assert len(p._entries) == 1
        p._clear()
        _process()
        assert len(p._entries) == 0

    def test_filter_combo_changes(self):
        p = self._make_panel()
        # Add entries of each type
        req = MagicMock()
        req.method = "GET"
        req.url = "https://example.com"
        req.headers = {}
        req.params = {}
        req.body = None
        p.log_request(req)

        resp = MagicMock()
        resp.status_code = 200
        resp.reason = "OK"
        resp.elapsed = 0.1
        resp.size = 100
        resp.headers = {}
        p.log_response(req, resp)

        p.log_error(req, "timeout")
        _process()

        # Switch filter to Requests
        p.filter_combo.setCurrentText("Requests")
        _process()
        # Switch filter to Responses
        p.filter_combo.setCurrentText("Responses")
        _process()
        # Switch filter to Errors
        p.filter_combo.setCurrentText("Errors")
        _process()
        # Back to All
        p.filter_combo.setCurrentText("All")
        _process()

    def test_show_detail(self):
        p = self._make_panel()
        req = MagicMock()
        req.method = "GET"
        req.url = "https://example.com"
        req.headers = {"Accept": "application/json"}
        req.params = {}
        req.body = None
        p.log_request(req)
        _process()
        # Select first item
        p.list_widget.setCurrentRow(0)
        _process()

    def test_passes_filter_all(self):
        p = self._make_panel()
        p.filter_combo.setCurrentText("All")
        assert p._passes_filter({"type": "request"}) is True
        assert p._passes_filter({"type": "response"}) is True
        assert p._passes_filter({"type": "error"}) is True

    def test_passes_filter_requests(self):
        p = self._make_panel()
        p.filter_combo.setCurrentText("Requests")
        assert p._passes_filter({"type": "request"}) is True
        assert p._passes_filter({"type": "response"}) is False

    def test_max_log_entries_eviction(self):
        from equinox.gui.logging_panel import MAX_LOG_ENTRIES

        p = self._make_panel()
        req = MagicMock()
        req.method = "GET"
        req.url = "https://example.com"
        req.headers = {}
        req.params = {}
        req.body = None
        # Push more than MAX_LOG_ENTRIES
        for _ in range(MAX_LOG_ENTRIES + 5):
            p.log_request(req)
        _process()
        assert len(p._entries) <= MAX_LOG_ENTRIES


# ─────────────────────────────────────────────────────────────────────────────
# IntelligencePanel
# ─────────────────────────────────────────────────────────────────────────────


class TestIntelligencePanel:
    def test_instantiate(self):
        from equinox.gui.response_panel.intelligence_panel import IntelligencePanel

        p = IntelligencePanel()
        assert p is not None

    def test_set_analyzing(self):
        from equinox.gui.response_panel.intelligence_panel import IntelligencePanel

        p = IntelligencePanel()
        p.set_analyzing()
        _process()

    def test_display_findings_empty(self):
        from equinox.gui.response_panel.intelligence_panel import IntelligencePanel

        p = IntelligencePanel()
        p.display_findings([])
        _process()

    def test_display_findings_with_findings(self):
        from equinox.core.response_intelligence.models import Category, Finding, Severity
        from equinox.gui.response_panel.intelligence_panel import IntelligencePanel

        p = IntelligencePanel()
        findings = [
            Finding(
                category=Category.SECURITY,
                severity=Severity.INFO,
                title="Test Finding",
                description="Test description",
                analyzer_id="test_analyzer",
                details={"key": "value"},
            ),
            Finding(
                category=Category.PERFORMANCE,
                severity=Severity.WARNING,
                title="Warning Finding",
                description="Warning description",
                analyzer_id="perf_analyzer",
            ),
            Finding(
                category=Category.CONSISTENCY,
                severity=Severity.CRITICAL,
                title="Critical Finding",
                description="Critical description",
                analyzer_id="cons_analyzer",
            ),
        ]
        p.display_findings(findings)
        _process()

    def test_finding_card_toggle(self):
        from equinox.core.response_intelligence.models import Category, Finding, Severity
        from equinox.gui.response_panel.intelligence_panel import _FindingCard

        finding = Finding(
            category=Category.SECURITY,
            severity=Severity.INFO,
            title="Test",
            description="Desc",
            analyzer_id="test_id",
            details={"extra": "data"},
        )
        card = _FindingCard(finding)
        # Toggle the details
        card._toggle_details()
        _process()
        assert card._expanded is True
        card._toggle_details()
        _process()
        assert card._expanded is False

    def test_finding_card_no_details(self):
        from equinox.core.response_intelligence.models import Category, Finding, Severity
        from equinox.gui.response_panel.intelligence_panel import _FindingCard

        finding = Finding(
            category=Category.SECURITY,
            severity=Severity.INFO,
            title="Simple",
            description="No details",
            analyzer_id="simple_id",
        )
        card = _FindingCard(finding)
        assert card._toggle_btn is None

    def test_finding_card_recommendation_visible(self):
        from equinox.core.response_intelligence.models import Category, Finding, Severity
        from equinox.gui.response_panel.intelligence_panel import _FindingCard

        finding = Finding(
            category=Category.SECURITY,
            severity=Severity.WARNING,
            title="Use secure cookies",
            description="Cookie flags are missing.",
            analyzer_id="security.cookie_flags",
            recommendation="Enable Secure, HttpOnly, and SameSite.",
        )
        card = _FindingCard(finding)

        labels = card.findChildren(QLabel)
        texts = [lbl.text() for lbl in labels if isinstance(lbl, QLabel)]
        assert any("Suggested action:" in text for text in texts)


# ─────────────────────────────────────────────────────────────────────────────
# IntelligenceWorker
# ─────────────────────────────────────────────────────────────────────────────


class TestIntelligenceWorker:
    def test_instantiate(self, db):
        from equinox.core.request import Request
        from equinox.gui.intelligence_worker import IntelligenceWorker

        req = Request(method="GET", url="https://example.com/api")
        resp = MagicMock()
        resp.status_code = 200
        resp.reason = "OK"
        resp.elapsed = 0.1
        resp.size = 100
        resp.headers = {}
        resp.body = b"{}"
        resp.sent_url = "https://example.com/api"
        resp.is_json = True
        resp.json = MagicMock(return_value={"key": "value"})
        resp.request = req
        worker = IntelligenceWorker(request=req, response=resp, db=db)
        assert worker is not None
        assert worker._request is req
        assert worker._response is resp

    def test_run_emits_finished(self, db):
        from equinox.core.request import Request
        from equinox.gui.intelligence_worker import IntelligenceWorker

        req = Request(method="GET", url="https://example.com/api")
        resp = MagicMock()
        resp.status_code = 200
        resp.reason = "OK"
        resp.elapsed = 0.1
        resp.size = 100
        resp.headers = {}
        resp.body = b"{}"
        resp.sent_url = "https://example.com/api"
        resp.is_json = False
        resp.request = req

        results = []
        worker = IntelligenceWorker(request=req, response=resp, db=db)
        worker.finished.connect(lambda f: results.append(f))
        worker.run()
        _process()
        assert len(results) == 1
        assert isinstance(results[0], list)

    def test_recommender_hints_disabled_by_settings(self, db):
        from equinox.core.request import Request
        from equinox.gui.intelligence_worker import IntelligenceWorker

        req = Request(method="GET", url="https://example.com/api")
        resp = MagicMock()
        resp.status_code = 200
        resp.reason = "OK"
        resp.elapsed = 0.1
        resp.size = 100
        resp.headers = {}
        resp.body = b"{}"
        resp.sent_url = "https://example.com/api"
        resp.is_json = True
        resp.json = MagicMock(return_value={"ok": True})
        resp.request = req

        worker = IntelligenceWorker(
            request=req,
            response=resp,
            db=db,
            disabled_analyzers={"recommender"},
        )
        assert worker._run_recommender_hints() == []


# ─────────────────────────────────────────────────────────────────────────────
# WebSocketPanel
# ─────────────────────────────────────────────────────────────────────────────


class TestWebSocketPanel:
    def test_instantiate(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        assert p is not None

    def test_has_url_input(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        assert hasattr(p, "url_input")

    def test_connect_button_exists(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        assert hasattr(p, "connect_btn")

    def test_send_button_exists(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        assert hasattr(p, "send_btn")

    def test_set_url(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        p.url_input.setText("wss://echo.example.com")
        assert p.url_input.text() == "wss://echo.example.com"

    def test_initial_state_disconnected(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        _process()
        # Connect button should be enabled at start
        assert p.connect_btn.isEnabled()
        # Send button should be disabled initially (not connected)
        assert not p.send_btn.isEnabled()

    def test_add_message_to_table(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        p._on_message("in", "Hello from server")
        _process()
        assert p.message_log.rowCount() == 1

    def test_add_outgoing_message(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        p._on_message("out", "Hello to server")
        _process()
        assert p.message_log.rowCount() == 1

    def test_clear_messages(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        p._on_message("in", "msg1")
        p._on_message("out", "msg2")
        _process()
        assert p.message_log.rowCount() == 2
        p._clear_log()
        _process()
        assert p.message_log.rowCount() == 0

    def test_on_connected(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        p._on_connected()
        _process()
        assert p.send_btn.isEnabled()

    def test_on_disconnected(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        p._on_connected()
        p._on_disconnected()
        _process()
        assert not p.send_btn.isEnabled()

    def test_on_error(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        p._on_error("Connection refused")
        _process()
        assert p.message_log.rowCount() == 1

    def test_format_json_message(self):
        from equinox.gui.websocket_panel import WebSocketPanel

        p = WebSocketPanel()
        p._fmt_json_check.setChecked(True)
        p._on_message("in", '{"key":"value"}')
        _process()
        assert p.message_log.rowCount() == 1


# ─────────────────────────────────────────────────────────────────────────────
# CookiesPanel
# ─────────────────────────────────────────────────────────────────────────────


class TestCookiesPanel:
    def test_instantiate(self, db):
        from equinox.gui.cookies_panel import CookiesPanel

        p = CookiesPanel(db)
        assert p is not None

    def test_refresh_empty(self, db):
        from equinox.gui.cookies_panel import CookiesPanel

        p = CookiesPanel(db)
        p.refresh()
        _process()
        assert p.table.rowCount() == 0

    def test_has_buttons(self, db):
        from equinox.gui.cookies_panel import CookiesPanel

        p = CookiesPanel(db)
        assert hasattr(p, "add_btn")
        assert hasattr(p, "delete_btn")
        assert hasattr(p, "clear_btn")

    def test_add_cookie_dialog_class(self):
        from equinox.gui.cookies_panel import _AddCookieDialog

        dlg = _AddCookieDialog()
        # Check form widgets
        assert dlg.name_edit is not None
        assert dlg.value_edit is not None
        assert dlg.domain_edit is not None

    def test_add_cookie_dialog_values(self):
        from equinox.gui.cookies_panel import _AddCookieDialog

        dlg = _AddCookieDialog()
        dlg.name_edit.setText("session_id")
        dlg.value_edit.setText("abc123")
        dlg.domain_edit.setText("example.com")
        dlg.path_edit.setText("/api")
        dlg.secure_cb.setChecked(True)
        vals = dlg.values()
        assert vals["name"] == "session_id"
        assert vals["value"] == "abc123"
        assert vals["domain"] == "example.com"
        assert vals["path"] == "/api"
        assert vals["secure"] is True

    def test_add_cookie_dialog_default_path(self):
        from equinox.gui.cookies_panel import _AddCookieDialog

        dlg = _AddCookieDialog()
        dlg.name_edit.setText("x")
        dlg.value_edit.setText("y")
        dlg.path_edit.clear()  # empty → defaults to "/"
        vals = dlg.values()
        assert vals["path"] == "/"


# ─────────────────────────────────────────────────────────────────────────────
# HistoryPanel
# ─────────────────────────────────────────────────────────────────────────────


class TestHistoryPanel:
    def test_instantiate(self, db):
        from equinox.gui.history_panel import HistoryPanel

        p = HistoryPanel(db)
        assert p is not None

    def test_refresh_empty(self, db):
        from equinox.gui.history_panel import HistoryPanel

        p = HistoryPanel(db)
        p.refresh()
        _process()
        assert p.list_widget.count() == 0

    def test_has_search_input(self, db):
        from equinox.gui.history_panel import HistoryPanel

        p = HistoryPanel(db)
        assert hasattr(p, "search_input")

    def test_method_filter(self, db):
        from equinox.gui.history_panel import HistoryPanel

        p = HistoryPanel(db)
        p.method_filter.setCurrentText("GET")
        _process()

    def test_status_filter(self, db):
        from equinox.gui.history_panel import HistoryPanel

        p = HistoryPanel(db)
        p.status_filter.setCurrentText("2xx")
        _process()

    def test_search_changes(self, db):
        from equinox.gui.history_panel import HistoryPanel

        p = HistoryPanel(db)
        p.search_input.setText("example")
        _process()

    def test_auto_refresh_toggle(self, db):
        from equinox.gui.history_panel import HistoryPanel

        p = HistoryPanel(db)
        p.auto_refresh_checkbox.setChecked(False)
        _process()
        assert not p.auto_refresh_enabled
        p.auto_refresh_checkbox.setChecked(True)
        _process()
        assert p.auto_refresh_enabled

    def test_advanced_filter_toggle(self, db):
        from equinox.gui.history_panel import HistoryPanel

        p = HistoryPanel(db)
        # The toggle button text changes — reliable even without a shown window
        p.advanced_toggle.setChecked(True)
        _process()
        assert "▼" in p.advanced_toggle.text()
        p.advanced_toggle.setChecked(False)
        _process()
        assert "▶" in p.advanced_toggle.text()

    def test_with_history_entries(self, db):
        from equinox.core.request import Request, Response
        from equinox.gui.history_panel import HistoryPanel
        from equinox.storage import HistoryManager

        mgr = HistoryManager(db)
        # Save a few history entries
        for i in range(3):
            req = Request(method="GET", url=f"https://example.com/api/{i}")
            resp = Response(
                status_code=200,
                reason="OK",
                headers={},
                body=b"{}",
                elapsed=0.1,
                request=req,
            )
            mgr.save_history(req, resp)
        p = HistoryPanel(db)
        p.refresh()
        _process()
        assert p.list_widget.count() > 0

    def test_apply_method_filter_with_entries(self, db):
        from equinox.core.request import Request, Response
        from equinox.gui.history_panel import HistoryPanel
        from equinox.storage import HistoryManager

        mgr = HistoryManager(db)
        req_get = Request(method="GET", url="https://example.com/get")
        req_post = Request(method="POST", url="https://example.com/post")
        resp_get = Response(
            status_code=200,
            reason="OK",
            headers={},
            body=b"{}",
            elapsed=0.1,
            request=req_get,
        )
        resp_post = Response(
            status_code=201,
            reason="Created",
            headers={},
            body=b"{}",
            elapsed=0.2,
            request=req_post,
        )
        mgr.save_history(req_get, resp_get)
        mgr.save_history(req_post, resp_post)
        p = HistoryPanel(db)
        p.refresh()
        _process()
        # Filter by GET
        p.method_filter.setCurrentText("GET")
        p._apply_filters()
        _process()

    def test_signals_exist(self, db):
        from equinox.gui.history_panel import HistoryPanel

        p = HistoryPanel(db)
        assert hasattr(p, "history_selected")
        assert hasattr(p, "history_replay")


# ─────────────────────────────────────────────────────────────────────────────
# VariablesPanel
# ─────────────────────────────────────────────────────────────────────────────


class TestVariablesPanel:
    def test_instantiate(self, db):
        from equinox.gui.variables_panel import VariablesPanel

        p = VariablesPanel(db)
        assert p is not None

    def test_refresh(self, db):
        from equinox.gui.variables_panel import VariablesPanel

        p = VariablesPanel(db)
        p.refresh()
        _process()

    def test_refresh_session_vars(self, db):
        from equinox.gui.variables_panel import VariablesPanel

        p = VariablesPanel(db)
        p.refresh_session_vars({"TOKEN": "abc123", "USER_ID": "42"})
        _process()

    def test_has_group_list(self, db):
        from equinox.gui.variables_panel import VariablesPanel

        p = VariablesPanel(db)
        assert hasattr(p, "groups_list")

    def test_create_group(self, db):
        from equinox.gui.variables_panel import VariablesPanel

        p = VariablesPanel(db)
        from equinox.storage import VariableGroupManager

        mgr = VariableGroupManager(db)
        mgr.create_group("Test Group", "Test description")
        p.refresh_groups()
        _process()
        assert p.groups_list.count() > 0

    def test_variable_dialog_instantiate(self):
        from equinox.gui.variables_panel import VariableDialog

        dlg = VariableDialog()
        assert dlg is not None

    def test_variable_dialog_with_values(self):
        from equinox.gui.variables_panel import VariableDialog

        dlg = VariableDialog(key="MY_KEY", value="my_value", description="A test var")
        assert dlg.key_input.text() == "MY_KEY"
        assert dlg.value_input.text() == "my_value"

    def test_variable_dialog_get_values(self):
        from equinox.gui.variables_panel import VariableDialog

        dlg = VariableDialog()
        dlg.key_input.setText("API_URL")
        dlg.value_input.setText("https://api.example.com")
        dlg.description_input.setPlainText("The API base URL")
        key, value, desc = dlg.get_values()
        assert key == "API_URL"
        assert value == "https://api.example.com"
        assert "API base URL" in desc

    def test_signals_exist(self, db):
        from equinox.gui.variables_panel import VariablesPanel

        p = VariablesPanel(db)
        assert hasattr(p, "variables_changed")
        assert hasattr(p, "clear_session_requested")


# ─────────────────────────────────────────────────────────────────────────────
# ApiSpecExportService
# ─────────────────────────────────────────────────────────────────────────────


class TestApiSpecExportService:
    def test_uses_collection_facade_not_raw_manager(self, db):
        """Architecture-boundary regression test: this service must build a
        CollectionFacade (which itself owns the one CollectionManager
        construction), not construct CollectionManager directly."""
        from equinox.application.collections import CollectionFacade
        from equinox.gui.collection_panel._spec_export_service import ApiSpecExportService

        service = ApiSpecExportService(db)
        assert isinstance(service._mgr, CollectionFacade)

    def test_build_collection_payload(self, db):
        from equinox.gui.collection_panel._spec_export_service import ApiSpecExportService
        from equinox.storage import CollectionManager

        mgr = CollectionManager(db)
        collection_id = mgr.create_collection("My Collection")

        service = ApiSpecExportService(db)
        payload = service.build_collection_payload(collection_id)

        assert "My Collection" in payload.title
        assert "OpenAPI 3 (JSON)" in payload.variants
        assert "Postman v2.1 (JSON)" in payload.variants
