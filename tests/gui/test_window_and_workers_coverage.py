"""Coverage-boosting tests for MainWindow, workers, and app module."""

import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QCoreApplication, QPointF, Qt, QPoint
from PyQt6.QtGui import QMouseEvent

_APP = QApplication.instance() or QApplication([])


def _process():
    QCoreApplication.processEvents()


def _close_win(win):
    """Safely stop timers and close a MainWindow in headless tests."""
    try:
        win.history_panel.refresh_timer.stop()
    except Exception:
        pass
    try:
        win.history_panel.auto_refresh_enabled = False
    except Exception:
        pass
    win.close()
    _process()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUINOX_DB_PATH", str(tmp_path / "test.db"))
    from equinox.storage import get_db
    return get_db()


# ─────────────────────────────────────────────────────────────────────────────
# app.py — _qt_exception_hook
# ─────────────────────────────────────────────────────────────────────────────

class TestAppModule:
    def test_qt_exception_hook_no_crash(self):
        from equinox.gui.app import _qt_exception_hook
        with patch("equinox.gui.widgets.CopyableMessageBox.critical"):
            _qt_exception_hook(ValueError, ValueError("test error"), None)

    def test_qt_exception_hook_logs(self, caplog):
        import logging
        from equinox.gui.app import _qt_exception_hook
        with patch("equinox.gui.widgets.CopyableMessageBox.critical"):
            with caplog.at_level(logging.CRITICAL, logger="equinox.gui.app"):
                _qt_exception_hook(RuntimeError, RuntimeError("crash"), None)

    def test_qt_exception_hook_message_box_failure(self):
        """Hook must not crash even if CopyableMessageBox.critical raises."""
        from equinox.gui.app import _qt_exception_hook
        with patch("equinox.gui.widgets.CopyableMessageBox.critical", side_effect=RuntimeError("no display")):
            _qt_exception_hook(TypeError, TypeError("problem"), None)


# ─────────────────────────────────────────────────────────────────────────────
# workers.py
# ─────────────────────────────────────────────────────────────────────────────

class TestRequestWorker:
    def test_instantiate(self):
        from equinox.gui.workers import RequestWorker
        from equinox.core.request import Request
        req = Request(method="GET", url="https://example.com")
        worker = RequestWorker(req)
        assert worker is not None
        assert not worker._cancelled

    def test_cancel(self):
        from equinox.gui.workers import RequestWorker
        from equinox.core.request import Request
        req = Request(method="GET", url="https://example.com")
        worker = RequestWorker(req)
        worker.cancel()
        assert worker._cancelled

    def test_with_cookie_manager(self):
        from equinox.gui.workers import RequestWorker
        from equinox.core.request import Request
        req = Request(method="POST", url="https://example.com/api")
        cm = MagicMock()
        worker = RequestWorker(req, cookie_manager=cm)
        assert worker._cookie_manager is cm


class TestOAuthTokenTester:
    def test_instantiate(self):
        from equinox.gui.workers import OAuthTokenTester
        worker = OAuthTokenTester(
            token_url="https://auth.example.com/token",
            client_id="my-id",
            secret="my-secret",
            scope="read write",
            grant_type="client_credentials",
            extra_params={},
        )
        assert worker.token_url == "https://auth.example.com/token"
        assert worker.grant_type == "client_credentials"

    def test_signals_exist(self):
        from equinox.gui.workers import OAuthTokenTester
        worker = OAuthTokenTester(
            token_url="https://auth.example.com/token",
            client_id="id", secret="sec", scope="",
            grant_type="client_credentials", extra_params={},
        )
        assert hasattr(worker, "done")


class TestBenchmarkDialog:
    def _make_dlg(self):
        from equinox.gui.workers import BenchmarkDialog
        from equinox.core.request import Request
        return BenchmarkDialog(Request(method="GET", url="https://example.com"))

    def test_instantiate(self):
        assert self._make_dlg() is not None

    def test_has_count_spin(self):
        assert hasattr(self._make_dlg(), "_count_spin")

    def test_has_run_button(self):
        assert hasattr(self._make_dlg(), "_run_btn")

    def test_has_results_text(self):
        assert hasattr(self._make_dlg(), "_results")

    def test_count_spin_range(self):
        dlg = self._make_dlg()
        assert dlg._count_spin.minimum() == 1
        assert dlg._count_spin.maximum() == 1000
        assert dlg._count_spin.value() == 10

    def test_export_btn_initially_disabled(self):
        dlg = self._make_dlg()
        assert hasattr(dlg, "_export_btn")
        assert not dlg._export_btn.isEnabled()


# ─────────────────────────────────────────────────────────────────────────────
# MainWindow
# ─────────────────────────────────────────────────────────────────────────────

class TestMainWindow:
    def test_instantiate(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        assert win is not None
        _close_win(win)

    def test_has_panels(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        assert hasattr(win, "request_panel")
        assert hasattr(win, "response_panel")
        assert hasattr(win, "collections_panel")
        assert hasattr(win, "history_panel")
        assert hasattr(win, "variables_panel")
        assert hasattr(win, "logging_panel")
        _close_win(win)

    def test_has_status_bar(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        assert win.statusBar() is not None
        _close_win(win)

    def test_zoom_in(self, db):
        from equinox.gui.window import MainWindow
        from equinox.gui.theme import get_font_size, set_font_size, MAX_FONT_SIZE
        win = MainWindow(db)
        original = get_font_size()
        if original < MAX_FONT_SIZE:
            win._zoom_in()
            assert get_font_size() == original + 1
        set_font_size(original)
        _close_win(win)

    def test_zoom_out(self, db):
        from equinox.gui.window import MainWindow
        from equinox.gui.theme import get_font_size, set_font_size, MIN_FONT_SIZE
        win = MainWindow(db)
        original = get_font_size()
        if original > MIN_FONT_SIZE:
            win._zoom_out()
            assert get_font_size() == original - 1
        set_font_size(original)
        _close_win(win)

    def test_zoom_reset(self, db):
        from equinox.gui.window import MainWindow
        from equinox.gui.theme import DEFAULT_FONT_SIZE, get_font_size
        win = MainWindow(db)
        win._zoom_reset()
        assert get_font_size() == DEFAULT_FONT_SIZE
        _close_win(win)

    def test_new_request(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        win._new_request()
        _process()
        _close_win(win)

    def test_show_about(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        with patch("equinox.gui.window.QMessageBox.about"):
            win._show_about()
        _close_win(win)

    def test_show_env_menu(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        win._show_env_menu()
        _process()
        _close_win(win)

    def test_refresh_env_label_no_env(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        win._refresh_env_label()
        _process()
        _close_win(win)

    def test_refresh_env_label_with_env(self, db):
        from equinox.gui.window import MainWindow
        from equinox.storage import EnvironmentManager
        env_mgr = EnvironmentManager(db)
        env_id = env_mgr.create_environment("Dev", {})
        env_mgr.set_active_environment(env_id)
        win = MainWindow(db)
        win._refresh_env_label()
        _process()
        _close_win(win)

    def test_switch_environment(self, db):
        from equinox.gui.window import MainWindow
        from equinox.storage import EnvironmentManager
        env_mgr = EnvironmentManager(db)
        env_id = env_mgr.create_environment("Test", {})
        win = MainWindow(db)
        win._switch_environment(env_id)
        _process()
        _close_win(win)

    def test_on_response_received(self, db):
        from equinox.gui.window import MainWindow
        from equinox.core.request import Request, Response
        win = MainWindow(db)
        req = Request(method="GET", url="https://example.com")
        resp = Response(
            status_code=200, reason="OK",
            headers={"Content-Type": "application/json"},
            body=b'{"key": "value"}',
            elapsed=0.123, request=req,
        )
        win._on_response_received(resp)
        _process()
        _close_win(win)

    def test_request_from_history(self, db):
        from equinox.gui.window import MainWindow
        entry = {
            "method": "POST",
            "url": "https://example.com/api/users",
            "request_headers": {"Content-Type": "application/json"},
            "request_body": '{"name": "Alice"}',
        }
        req = MainWindow._request_from_history(entry)
        assert req.method == "POST"
        assert req.url == "https://example.com/api/users"

    def test_save_and_restore_layout(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        win._save_layout()
        win._restore_layout()
        _process()
        _close_win(win)

    def test_show_shortcuts_dialog(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        with patch("PyQt6.QtWidgets.QDialog.exec"):
            win._show_shortcuts_dialog()
        _close_win(win)

    def test_sync_theme_checks(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        win._sync_theme_checks()
        _process()
        _close_win(win)

    def test_open_log_file_no_log(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        with patch("equinox.gui.window.QMessageBox.information"):
            with patch("equinox.gui.log_file_actions.get_log_file", return_value=None):
                win._open_log_file()
        _close_win(win)

    def test_set_theme(self, db):
        from equinox.gui.window import MainWindow
        from equinox.gui.theme import get_theme_mode, set_theme_mode
        win = MainWindow(db)
        original = get_theme_mode()
        win._set_theme("dark")
        _process()
        set_theme_mode(original)
        _close_win(win)

    def test_manage_secret_managers_opens_dedicated_dialog(self, db):
        from equinox.gui.window import MainWindow

        win = MainWindow(db)
        with patch(
            "equinox.gui.dialogs.secret_manager_settings_dialog.SecretManagerSettingsDialog.exec",
            return_value=0,
        ):
            win._manage_secret_managers()
        _close_win(win)

    def test_menu_bar_created(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        assert win.menuBar() is not None
        assert len(win.menuBar().actions()) > 0
        _close_win(win)

    def test_menu_bar_has_window_controls(self, db):
        from equinox.gui.window import MainWindow

        win = MainWindow(db)
        assert hasattr(win, "_win_min_btn")
        assert hasattr(win, "_win_max_btn")
        assert hasattr(win, "_win_close_btn")
        assert win.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is not None
        _close_win(win)

    def test_menu_bar_shows_window_title(self, db):
        from equinox.gui.window import MainWindow

        win = MainWindow(db)
        assert hasattr(win, "_menu_title_label")
        assert win.menuBar().cornerWidget(Qt.Corner.TopLeftCorner) is not None
        assert win._menu_title_label.text() == win.windowTitle()

        win.setWindowTitle("Equinox Test Title")
        assert win._menu_title_label.text() == "Equinox Test Title"
        _close_win(win)

    def test_dragging_menu_bar_title_moves_window(self, db):
        from equinox.gui.window import MainWindow

        win = MainWindow(db)
        win.show()
        _process()
        win.move(120, 120)
        _process()

        title_label = win._menu_title_label
        start_pos = win.pos()
        local = QPointF(6.0, 6.0)
        global_start = QPointF(title_label.mapToGlobal(local.toPoint()))

        press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            local,
            global_start,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(title_label, press)

        global_end = QPointF(global_start.x() + 40.0, global_start.y() + 30.0)
        move = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            local,
            global_end,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(title_label, move)

        release = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            local,
            global_end,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(title_label, release)
        _process()

        assert win.pos() != start_pos
        _close_win(win)

    def test_window_controls_sync_on_state_change(self, db):
        from equinox.gui.window import MainWindow

        win = MainWindow(db)
        win.showMaximized()
        win._sync_window_controls()
        assert win._win_max_btn.toolTip() == "Restore"

        win.showNormal()
        win._sync_window_controls()
        assert win._win_max_btn.toolTip() == "Maximize"
        _close_win(win)

    def test_fetch_history_entry_not_found(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        result = win._fetch_history_entry(99999)
        assert result is None
        _close_win(win)

    def test_fetch_history_entry_found(self, db):
        from equinox.gui.window import MainWindow
        from equinox.storage import HistoryManager
        from equinox.core.request import Request, Response
        mgr = HistoryManager(db)
        req = Request(method="GET", url="https://example.com/api/test")
        resp = Response(
            status_code=200, reason="OK",
            headers={"Content-Type": "application/json"},
            body=b'{"ok": true}',
            elapsed=0.1, request=req,
        )
        history_id = mgr.save_history(req, resp)
        win = MainWindow(db)
        result = win._fetch_history_entry(history_id)
        assert result is not None
        assert result["method"] == "GET"
        _close_win(win)

    def test_run_intelligence_analysis(self, db):
        from equinox.gui.window import MainWindow
        from equinox.core.request import Request, Response
        win = MainWindow(db)
        req = Request(method="GET", url="https://example.com/api")
        resp = Response(
            status_code=200, reason="OK",
            headers={"Content-Type": "application/json"},
            body=b'{"data": [1, 2, 3]}',
            elapsed=0.05, request=req,
        )
        win._run_intelligence_analysis(resp)
        _process()
        if win._intelligence_worker is not None:
            try:
                win._intelligence_worker.finished.disconnect()
            except Exception:
                pass
        _close_win(win)

    def test_load_history_entry_not_found(self, db):
        from equinox.gui.window import MainWindow
        win = MainWindow(db)
        # Non-existent ID — should not crash
        win._load_history_entry(99999)
        _process()
        _close_win(win)

    def test_load_history_entry_with_response(self, db):
        from equinox.gui.window import MainWindow
        from equinox.storage import HistoryManager
        from equinox.core.request import Request, Response
        mgr = HistoryManager(db)
        req = Request(method="GET", url="https://example.com/api/test")
        resp = Response(
            status_code=200, reason="OK",
            headers={"Content-Type": "application/json"},
            body=b'{"ok": true}',
            elapsed=0.1, request=req
        )
        history_id = mgr.save_history(req, resp)

        win = MainWindow(db)
        win._load_history_entry(history_id)
        _process()
        _close_win(win)

    def test_resize_edges_detected_on_border(self, db):
        from equinox.gui.window import MainWindow

        win = MainWindow(db)
        win.resize(800, 600)

        left_top = win._resize_edges_for_pos(QPoint(1, 1))
        assert bool(left_top & Qt.Edge.LeftEdge)
        assert bool(left_top & Qt.Edge.TopEdge)

        right_bottom = win._resize_edges_for_pos(QPoint(win.width() - 1, win.height() - 1))
        assert bool(right_bottom & Qt.Edge.RightEdge)
        assert bool(right_bottom & Qt.Edge.BottomEdge)

        center = win._resize_edges_for_pos(QPoint(win.width() // 2, win.height() // 2))
        assert center == Qt.Edge(0)
        _close_win(win)

    def test_resize_cursor_changes_near_edges(self, db):
        from equinox.gui.window import MainWindow

        win = MainWindow(db)
        win.resize(800, 600)

        win._update_resize_cursor(QPoint(1, 1))
        assert win.cursor().shape() == Qt.CursorShape.SizeFDiagCursor

        win._update_resize_cursor(QPoint(win.width() // 2, win.height() // 2))
        assert win.cursor().shape() == Qt.CursorShape.ArrowCursor
        _close_win(win)

    def test_resize_cursor_forced_arrow_when_fullscreen(self, db):
        from equinox.gui.window import MainWindow

        win = MainWindow(db)
        with patch.object(win, "isFullScreen", return_value=True):
            win._update_resize_cursor(QPoint(1, 1))
            assert win.cursor().shape() == Qt.CursorShape.ArrowCursor
        _close_win(win)


class TestIntelligenceWorker:
    def test_execute_includes_recommender_hints(self, db):
        from equinox.core.request import Request, Response
        from equinox.gui.intelligence_worker import IntelligenceWorker

        req = Request(method="GET", url="https://example.com/api/users/42")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"Content-Type": "application/json"},
            body=b'{"ok": true}',
            elapsed=0.02,
            request=req,
        )
        worker = IntelligenceWorker(request=req, response=resp, db=db)

        suggestion = {
            "type": "header",
            "key": "x-trace-id",
            "suggested_value": "abc-123",
            "confidence": 0.9,
            "based_on": 3,
        }

        with patch.object(worker, "_fetch_analysis_context", return_value=MagicMock()):
            with patch.object(worker, "_run_engine", return_value=[]):
                with patch.object(worker, "_persist_results"):
                    with patch(
                        "equinox.gui.intelligence_worker.Recommender.generate_suggestions",
                        return_value=[suggestion],
                    ):
                        findings = worker._execute()

        assert any(f.analyzer_id == "recommender" for f in findings)

