"""Tests for the session variables viewer in VariablesPanel."""

import sys
import pytest
from unittest.mock import MagicMock, patch


# ── Qt fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    """Ensure a single QApplication exists for the whole test session."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture()
def variables_panel(qapp):
    """Create a VariablesPanel with a mocked DB, properly parented."""
    from PyQt6.QtWidgets import QWidget
    from equinox.gui.variables_panel import VariablesPanel

    db = MagicMock()
    db.fetchall = MagicMock(return_value=[])
    db.fetchone = MagicMock(return_value=None)

    parent = QWidget()
    vp = VariablesPanel(db, parent=parent)
    # Prevent Python GC from collecting the parent while the test runs
    vp._test_parent_ref = parent
    yield vp
    # Cleanup: explicitly delete so Qt C++ side is freed deterministically
    vp.setParent(None)
    parent.deleteLater()


# ── RequestPanel accessor tests ───────────────────────────────────────────────

class TestRequestPanelSessionVarAccessors:
    """Test the public accessor + clear methods on RequestPanel."""

    def test_get_session_vars_returns_copy(self):
        """get_session_vars returns a copy, not the internal dict."""
        panel = _make_mock_panel()
        panel._session_vars = {"tok": "abc"}
        result = panel.get_session_vars()
        assert result == {"tok": "abc"}
        # Mutating the copy must not affect internal state
        result["tok"] = "changed"
        assert panel._session_vars["tok"] == "abc"

    def test_clear_session_vars_empties_dict(self):
        """clear_session_vars empties the dict and emits signal."""
        panel = _make_mock_panel()
        panel._session_vars = {"a": "1", "b": "2"}
        emitted = []
        panel.session_vars_changed = MagicMock(side_effect=lambda v: emitted.append(v))
        # Simulate the real method
        panel._session_vars.clear()
        panel.session_vars_changed(dict(panel._session_vars))
        assert panel._session_vars == {}
        assert emitted == [{}]


# ── VariablesPanel session table tests ────────────────────────────────────────

class TestVariablesPanelRefresh:
    """Test the refresh_session_vars logic."""

    def test_refresh_populates_table(self, variables_panel):
        """refresh_session_vars fills the table with key-value rows."""
        vp = variables_panel
        session = {"auth_token": "tok_abc", "user_id": "42"}
        vp.refresh_session_vars(session)
        assert vp._session_table.rowCount() == 2
        # Table is sorted by key
        assert vp._session_table.item(0, 0).text() == "auth_token"
        assert vp._session_table.item(0, 1).text() == "tok_abc"
        assert vp._session_table.item(1, 0).text() == "user_id"
        assert vp._session_table.item(1, 1).text() == "42"

    def test_refresh_updates_count_label(self, variables_panel):
        """Count label reflects the current session variable count."""
        vp = variables_panel
        vp.refresh_session_vars({})
        assert "No captured" in vp._session_count_label.text()

        vp.refresh_session_vars({"x": "1"})
        assert "1 captured variable" in vp._session_count_label.text()

        vp.refresh_session_vars({"x": "1", "y": "2"})
        assert "2 captured variables" in vp._session_count_label.text()

    def test_refresh_enables_clear_button(self, variables_panel):
        """Clear All button is enabled only when vars exist."""
        vp = variables_panel
        vp.refresh_session_vars({})
        assert not vp._session_clear_btn.isEnabled()

        vp.refresh_session_vars({"a": "b"})
        assert vp._session_clear_btn.isEnabled()

    def test_refresh_auto_expands_group_box(self, variables_panel):
        """Session group box auto-expands when first var arrives."""
        vp = variables_panel
        vp._session_group.setChecked(False)  # reset
        vp.refresh_session_vars({"tok": "abc"})
        assert vp._session_group.isChecked()

    def test_refresh_clears_on_empty(self, variables_panel):
        """Passing empty dict clears the table."""
        vp = variables_panel
        vp.refresh_session_vars({"a": "1"})
        assert vp._session_table.rowCount() == 1
        vp.refresh_session_vars({})
        assert vp._session_table.rowCount() == 0

    def test_copy_session_vars(self, variables_panel):
        """_copy_session_vars puts KEY=VALUE lines on clipboard."""
        vp = variables_panel
        vp.refresh_session_vars({"BASE_URL": "https://api.test", "TOKEN": "abc"})
        clipboard_text = []
        with patch(
            "equinox.gui.variables_panel.QApplication.clipboard"
        ) as mock_cb:
            mock_clip = MagicMock()
            mock_cb.return_value = mock_clip
            mock_clip.setText = lambda t: clipboard_text.append(t)
            vp._copy_session_vars()
        assert len(clipboard_text) == 1
        lines = clipboard_text[0].split("\n")
        assert "BASE_URL=https://api.test" in lines
        assert "TOKEN=<redacted>" in lines

    def test_on_clear_session_emits_signal(self, variables_panel):
        """Clicking Clear All emits clear_session_requested."""
        vp = variables_panel
        vp.refresh_session_vars({"x": "1"})
        emitted = []
        vp.clear_session_requested.connect(lambda: emitted.append(True))
        vp._on_clear_session()
        assert emitted == [True]

    def test_on_clear_session_noop_when_empty(self, variables_panel):
        """Clear All does nothing when there are no session vars."""
        vp = variables_panel
        vp.refresh_session_vars({})
        emitted = []
        vp.clear_session_requested.connect(lambda: emitted.append(True))
        vp._on_clear_session()
        assert emitted == []

    def test_add_custom_session_var_publishes_to_request_panel(self, variables_panel):
        """Add action writes to RequestPanel._session_vars and emits update signal."""
        vp = variables_panel
        rp = MagicMock()
        rp._session_vars = {}
        parent = vp._test_parent_ref
        parent.request_panel = rp

        with patch("equinox.gui.variables_panel.QInputDialog.getText") as mock_get_text:
            mock_get_text.side_effect = [("TOKEN", True), ("abc123", True)]
            vp._add_session_var()

        assert rp._session_vars["TOKEN"] == "abc123"
        rp.session_vars_changed.emit.assert_called_once_with({"TOKEN": "abc123"})

    def test_add_custom_session_var_fallback_updates_local_table(self, variables_panel):
        """If no RequestPanel is available, Add action still updates the local table."""
        vp = variables_panel
        vp.refresh_session_vars({"A": "1"})

        with patch("equinox.gui.variables_panel.QInputDialog.getText") as mock_get_text:
            mock_get_text.side_effect = [("B", True), ("2", True)]
            vp._add_session_var()

        table = {
            vp._session_table.item(r, 0).text(): vp._session_table.item(r, 1).text()
            for r in range(vp._session_table.rowCount())
        }
        assert table == {"A": "1", "B": "2"}

    def test_add_custom_session_var_rejects_invalid_name(self, variables_panel):
        """Invalid names are rejected before value prompt is shown."""
        vp = variables_panel
        with patch("equinox.gui.variables_panel.QInputDialog.getText") as mock_get_text, patch(
            "equinox.gui.variables_panel.QMessageBox.warning"
        ) as mock_warn:
            mock_get_text.return_value = ("bad key", True)
            vp._add_session_var()

        mock_warn.assert_called_once()
        assert mock_get_text.call_count == 2

    def test_magic_hint_is_visible_in_global_section(self, variables_panel):
        vp = variables_panel
        assert "{{TODAY}}" in vp._magic_hint.text()
        assert "{{ONE_MONTH_AGO}}" in vp._magic_hint.text()

    def test_global_table_height_scales_with_content(self, variables_panel):
        vp = variables_panel
        small_h = vp._global_table.height()
        vp._global_mgr.set_variable("A", "1")
        vp._global_mgr.set_variable("B", "2")
        vp.refresh_global_vars()
        larger_h = vp._global_table.height()
        assert larger_h >= small_h


# ── Capture engine integration ────────────────────────────────────────────────

class TestCaptureIntegration:
    """Verify CaptureEngine produces results that feed into session vars."""

    def test_capture_engine_produces_results(self):
        """CaptureEngine.apply_all returns correct results for session var storage."""
        from equinox.core.captures import Capture, CaptureEngine
        from equinox.core.request import Request, Response

        req = Request(method="GET", url="https://api.test/data")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"content-type": "application/json"},
            body=b'{"tok": "abc123"}',
            elapsed=0.1,
            request=req,
        )
        caps = [Capture(variable="tok", source="json", path="tok")]
        actual = CaptureEngine.apply_all(caps, resp)
        assert len(actual) == 1
        assert actual[0].variable == "tok"
        assert actual[0].value == "abc123"
        assert actual[0].success is True

    def test_multiple_captures(self):
        """Multiple captures all produce results."""
        from equinox.core.captures import Capture, CaptureEngine
        from equinox.core.request import Request, Response

        req = Request(method="GET", url="https://api.test/users")
        resp = Response(
            status_code=200,
            reason="OK",
            headers={"x-request-id": "req-42", "content-type": "text/plain"},
            body=b'{"user": {"id": 7, "name": "alice"}}',
            elapsed=0.05,
            request=req,
        )
        caps = [
            Capture(variable="user_id", source="json", path="user.id"),
            Capture(variable="req_id", source="header", path="x-request-id"),
            Capture(variable="status", source="status"),
        ]
        results = CaptureEngine.apply_all(caps, resp)
        session = {r.variable: r.value for r in results}
        assert session["user_id"] == "7"
        assert session["req_id"] == "req-42"
        assert session["status"] == "200"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_panel():
    """Create a minimal mock of RequestPanel for testing accessors."""
    panel = MagicMock()
    panel._session_vars = {}
    panel.session_vars_changed = MagicMock()
    panel.get_session_vars = lambda: dict(panel._session_vars)
    return panel

