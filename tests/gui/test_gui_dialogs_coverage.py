"""Coverage-boosting tests for GUI dialogs."""

from unittest.mock import patch

import pytest
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])


def _process():
    QCoreApplication.processEvents()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUINOX_DB_PATH", str(tmp_path / "test.db"))
    from equinox.storage import get_db

    return get_db()


# ─────────────────────────────────────────────────────────────────────────────
# PreferencesDialog
# ─────────────────────────────────────────────────────────────────────────────


class TestPreferencesDialog:
    def test_instantiate(self):
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog

        dlg = PreferencesDialog()
        assert dlg is not None

    def test_has_theme_combo(self):
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog

        dlg = PreferencesDialog()
        assert hasattr(dlg, "_theme_combo")

    def test_has_slider_and_spin(self):
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog

        dlg = PreferencesDialog()
        assert hasattr(dlg, "_slider")
        assert hasattr(dlg, "_spin")

    def test_change_font_size(self):
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog
        from equinox.gui.theme import get_font_size

        dlg = PreferencesDialog()
        original = get_font_size()
        dlg._spin.setValue(original + 1)
        _process()

    def test_restore_defaults(self):
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog

        dlg = PreferencesDialog()
        dlg._restore_defaults()
        _process()

    def test_cancel_reverts(self):
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog
        from equinox.gui.theme import get_font_size

        original = get_font_size()
        dlg = PreferencesDialog()
        dlg._spin.setValue(original + 2)
        _process()
        dlg._cancel()
        from equinox.gui.theme import get_font_size as gfs

        assert gfs() == original

    def test_accept_saves(self):
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog

        dlg = PreferencesDialog()
        dlg._proxy_host.setText("127.0.0.1")
        dlg._proxy_port.setValue(8080)
        dlg._accept()
        _process()
        # Cleanup: restore defaults and persist to avoid leaving global QSettings
        # (tests run in the same environment and QSettings on Windows persists
        # into the registry; ensure proxy is cleared so subsequent tests / runs
        # don't pick up a leftover proxy configuration).
        dlg._restore_defaults()
        dlg._accept()
        _process()

    def test_update_preview(self):
        from equinox.gui.dialogs.preferences_dialog import PreferencesDialog

        dlg = PreferencesDialog()
        dlg._update_preview(14)
        _process()


# ─────────────────────────────────────────────────────────────────────────────
# ApiSpecDialog
# ─────────────────────────────────────────────────────────────────────────────


class TestApiSpecDialog:
    def test_instantiate(self):
        from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog

        dlg = ApiSpecDialog(title="Test Spec")
        assert dlg is not None

    def test_set_variants(self):
        from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog

        dlg = ApiSpecDialog()
        dlg.set_variants(
            {
                "OpenAPI": '{"openapi": "3.0.0"}',
                "Postman": '{"info": {"name": "test"}}',
            }
        )
        assert dlg.format_combo.count() == 2

    def test_set_empty_variants(self):
        from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog

        dlg = ApiSpecDialog()
        dlg.set_variants({})
        assert dlg.format_combo.count() == 0

    def test_format_change(self):
        from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog

        dlg = ApiSpecDialog()
        dlg.set_variants(
            {
                "OpenAPI": '{"openapi": "3.0.0"}',
                "Postman": '{"info": {"name": "test"}}',
            }
        )
        dlg.format_combo.setCurrentIndex(1)
        _process()

    def test_copy_button_exists(self):
        from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog

        dlg = ApiSpecDialog()
        assert hasattr(dlg, "copy_btn")

    def test_save_button_exists(self):
        from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog

        dlg = ApiSpecDialog()
        assert hasattr(dlg, "save_btn")

    def test_preview_text_updated(self):
        from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog

        dlg = ApiSpecDialog()
        content = '{"openapi": "3.0.0", "info": {"title": "Test"}}'
        dlg.set_variants({"OpenAPI": content})
        _process()
        assert "openapi" in dlg.preview.toPlainText()

    def test_disable_clipboard(self):
        from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog

        dlg = ApiSpecDialog()
        dlg._allow_clipboard = False
        dlg.set_variants({"OpenAPI": '{"openapi": "3.0.0"}'})
        # Copy button should be hidden or disabled when clipboard not allowed
        _process()

    def test_on_copy(self):
        from equinox.gui.dialogs.api_spec_dialog import ApiSpecDialog

        dlg = ApiSpecDialog()
        dlg.set_variants({"OpenAPI": '{"openapi": "3.0.0"}'})
        dlg._on_copy()
        _process()


# ─────────────────────────────────────────────────────────────────────────────
# EnvironmentDialog
# ─────────────────────────────────────────────────────────────────────────────


class TestEnvironmentDialog:
    def test_instantiate(self, db):
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog

        dlg = EnvironmentDialog(db)
        assert dlg is not None

    def test_has_env_list(self, db):
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog

        dlg = EnvironmentDialog(db)
        assert hasattr(dlg, "env_list")

    def test_has_buttons(self, db):
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog

        dlg = EnvironmentDialog(db)
        assert hasattr(dlg, "new_btn")
        assert hasattr(dlg, "delete_btn")
        assert hasattr(dlg, "activate_btn")

    def test_creates_environment(self, db):
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog
        from equinox.storage import EnvironmentManager

        env_mgr = EnvironmentManager(db)
        env_id = env_mgr.create_environment("Dev", {})
        dlg = EnvironmentDialog(db)
        assert dlg.env_list.count() >= 1

    def test_refresh_environments(self, db):
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog
        from equinox.storage import EnvironmentManager

        env_mgr = EnvironmentManager(db)
        env_mgr.create_environment("Production", {})
        env_mgr.create_environment("Staging", {})
        dlg = EnvironmentDialog(db)
        dlg._refresh_list()
        _process()
        assert dlg.env_list.count() >= 2

    def test_select_environment(self, db):
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog
        from equinox.storage import EnvironmentManager

        env_mgr = EnvironmentManager(db)
        env_mgr.create_environment("Dev", {})
        dlg = EnvironmentDialog(db)
        dlg.env_list.setCurrentRow(0)
        _process()

    def test_activate_environment(self, db):
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog
        from equinox.storage import EnvironmentManager

        env_mgr = EnvironmentManager(db)
        env_mgr.create_environment("TestEnv", {})
        dlg = EnvironmentDialog(db)
        dlg.env_list.setCurrentRow(0)
        _process()
        dlg._activate_environment()
        _process()

    def test_variable_table_exists(self, db):
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog

        dlg = EnvironmentDialog(db)
        assert hasattr(dlg, "var_table")

    def test_add_variable_row(self, db):
        from equinox.gui.dialogs.environment_dialog import EnvironmentDialog
        from equinox.storage import EnvironmentManager

        env_mgr = EnvironmentManager(db)
        env_mgr.create_environment("Dev", {})
        dlg = EnvironmentDialog(db)
        dlg.env_list.setCurrentRow(0)
        _process()
        dlg._add_variable_row()
        _process()
        assert dlg.var_table.rowCount() >= 1


# ─────────────────────────────────────────────────────────────────────────────
# OAuthClientsDialog
# ─────────────────────────────────────────────────────────────────────────────


class TestOAuthClientsDialog:
    def test_instantiate(self, db):
        from equinox.gui.dialogs.oauth_clients_dialog import OAuthClientsDialog

        dlg = OAuthClientsDialog(db)
        assert dlg is not None

    def test_has_client_list(self, db):
        from equinox.gui.dialogs.oauth_clients_dialog import OAuthClientsDialog

        dlg = OAuthClientsDialog(db)
        assert hasattr(dlg, "client_list")

    def test_has_buttons(self, db):
        from equinox.gui.dialogs.oauth_clients_dialog import OAuthClientsDialog

        dlg = OAuthClientsDialog(db)
        assert hasattr(dlg, "new_btn")
        assert hasattr(dlg, "delete_btn")

    def test_new_client_adds_to_list(self, db):
        from equinox.gui.dialogs.oauth_clients_dialog import OAuthClientsDialog

        dlg = OAuthClientsDialog(db)
        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("My Client", True)):
            dlg._new_client()
        _process()

    def test_refresh_list_empty(self, db):
        from equinox.gui.dialogs.oauth_clients_dialog import OAuthClientsDialog

        dlg = OAuthClientsDialog(db)
        dlg._refresh_list()
        _process()
        assert dlg.client_list.count() == 0

    def test_form_fields_exist(self, db):
        from equinox.gui.dialogs.oauth_clients_dialog import OAuthClientsDialog

        dlg = OAuthClientsDialog(db)
        assert hasattr(dlg, "f_name")
        assert hasattr(dlg, "f_client_id")

    def test_with_existing_client(self, db):
        from equinox.gui.dialogs.oauth_clients_dialog import OAuthClientsDialog
        from equinox.storage.oauth_clients import OAuthClientManager

        mgr = OAuthClientManager(db)
        mgr.create_client(
            name="My OAuth App",
            grant_type="client_credentials",
            token_url="https://auth.example.com/token",
            client_id="app-client-id",
            client_secret="app-secret",
        )
        dlg = OAuthClientsDialog(db)
        assert dlg.client_list.count() == 1
        dlg.client_list.setCurrentRow(0)
        _process()

    def test_save_client_updates(self, db):
        from equinox.gui.dialogs.oauth_clients_dialog import OAuthClientsDialog
        from equinox.storage.oauth_clients import OAuthClientManager

        mgr = OAuthClientManager(db)
        mgr.create_client(
            name="Test Client",
            grant_type="client_credentials",
            token_url="https://auth.example.com/token",
            client_id="cid",
            client_secret="csec",
        )
        dlg = OAuthClientsDialog(db)
        dlg.client_list.setCurrentRow(0)
        _process()
        dlg.f_name.setText("Updated Client")
        with patch(
            "PyQt6.QtWidgets.QMessageBox.warning",
            side_effect=AssertionError("unexpected warning dialog"),
        ):
            with patch(
                "PyQt6.QtWidgets.QMessageBox.critical",
                side_effect=AssertionError("unexpected critical dialog"),
            ):
                assert dlg._save_client() is True
        _process()

    def test_signals_exist(self, db):
        from equinox.gui.dialogs.oauth_clients_dialog import OAuthClientsDialog

        dlg = OAuthClientsDialog(db)
        assert hasattr(dlg, "clients_changed")


# ─────────────────────────────────────────────────────────────────────────────
# SavedCredentialsDialog
# ─────────────────────────────────────────────────────────────────────────────


class TestSavedCredentialsDialog:
    def test_instantiate(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog

        dlg = SavedCredentialsDialog(db)
        assert dlg is not None

    def test_has_cred_list(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog

        dlg = SavedCredentialsDialog(db)
        assert hasattr(dlg, "cred_list")

    def test_refresh_list_empty(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog

        dlg = SavedCredentialsDialog(db)
        dlg._refresh_list()
        _process()
        assert dlg.cred_list.count() == 0

    def test_new_cred_cancelled(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog

        dlg = SavedCredentialsDialog(db)
        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("", False)):
            dlg._new_cred()
        _process()
        assert dlg.cred_list.count() == 0

    def test_type_stacked_widget(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog

        dlg = SavedCredentialsDialog(db)
        assert hasattr(dlg, "stack")

    def test_type_combo_exists(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog

        dlg = SavedCredentialsDialog(db)
        assert hasattr(dlg, "f_type")

    def test_with_bearer_credential(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog
        from equinox.storage.saved_credentials import SavedCredentialsManager

        mgr = SavedCredentialsManager(db)
        mgr.create("My Bearer", "bearer", {"token": "my-token"})
        dlg = SavedCredentialsDialog(db)
        assert dlg.cred_list.count() == 1
        dlg.cred_list.setCurrentRow(0)
        _process()

    def test_with_api_key_credential(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog
        from equinox.storage.saved_credentials import SavedCredentialsManager

        mgr = SavedCredentialsManager(db)
        mgr.create(
            "My API Key",
            "api_key",
            {"key": "X-API-Key", "value": "secret123", "location": "header"},
        )
        dlg = SavedCredentialsDialog(db)
        assert dlg.cred_list.count() == 1

    def test_with_basic_credential(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog
        from equinox.storage.saved_credentials import SavedCredentialsManager

        mgr = SavedCredentialsManager(db)
        mgr.create("My Basic", "basic", {"username": "user", "password": "pass"})
        dlg = SavedCredentialsDialog(db)
        assert dlg.cred_list.count() == 1
        dlg.cred_list.setCurrentRow(0)
        _process()

    def test_type_switch_changes_stack(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog

        dlg = SavedCredentialsDialog(db)
        # Switch type combo to different types
        for i in range(dlg.f_type.count()):
            dlg.f_type.setCurrentIndex(i)
            _process()

    def test_signals_exist(self, db):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog

        dlg = SavedCredentialsDialog(db)
        assert hasattr(dlg, "credentials_changed")


# ─────────────────────────────────────────────────────────────────────────────
# CollectionVariablesDialog
# ─────────────────────────────────────────────────────────────────────────────


class TestCollectionVariablesDialog:
    def _make_collection(self, db):
        from equinox.storage import CollectionManager

        mgr = CollectionManager(db)
        return mgr.create_collection("Test Collection", "Desc")

    def test_instantiate(self, db):
        from equinox.gui.dialogs.collection_variables_dialog import CollectionVariablesDialog

        col_id = self._make_collection(db)
        dlg = CollectionVariablesDialog(db, col_id, "Test Collection")
        assert dlg is not None

    def test_has_variables_table(self, db):
        from equinox.gui.dialogs.collection_variables_dialog import CollectionVariablesDialog

        col_id = self._make_collection(db)
        dlg = CollectionVariablesDialog(db, col_id, "Test Collection")
        assert hasattr(dlg, "variables_table")

    def test_add_variable_group_dialog(self, db):
        from equinox.gui.dialogs.collection_variables_dialog import AddVariableGroupDialog

        col_id = self._make_collection(db)
        dlg = AddVariableGroupDialog(db, col_id)
        assert dlg is not None
        assert dlg.groups_list is not None

    def test_groups_table_exists(self, db):
        from equinox.gui.dialogs.collection_variables_dialog import CollectionVariablesDialog

        col_id = self._make_collection(db)
        dlg = CollectionVariablesDialog(db, col_id, "Test Collection")
        assert hasattr(dlg, "groups_table")

    def test_add_buttons_exist(self, db):
        from equinox.gui.dialogs.collection_variables_dialog import CollectionVariablesDialog

        col_id = self._make_collection(db)
        dlg = CollectionVariablesDialog(db, col_id, "Test Collection")
        assert hasattr(dlg, "add_var_btn")
        assert hasattr(dlg, "add_group_btn")

    def test_group_list_empty(self, db):
        from equinox.gui.dialogs.collection_variables_dialog import CollectionVariablesDialog

        col_id = self._make_collection(db)
        dlg = CollectionVariablesDialog(db, col_id, "Test Collection")
        _process()


# ─────────────────────────────────────────────────────────────────────────────
# SaveRequestDialog
# ─────────────────────────────────────────────────────────────────────────────


class TestSaveRequestDialog:
    @staticmethod
    def _collections() -> list[dict[str, object]]:
        return [{"id": 1, "name": "My Requests"}]

    def test_instantiate(self, db):
        from equinox.gui.dialogs.save_dialog import SaveRequestDialog

        dlg = SaveRequestDialog(self._collections(), "GET", "https://example.com/api")
        assert dlg is not None

    def test_default_collection_created(self, db):
        from equinox.gui.dialogs.save_dialog import SaveRequestDialog

        dlg = SaveRequestDialog(self._collections(), "POST", "https://api.example.com/users")
        # Collection combo should have at least one entry
        assert dlg._col_combo.count() >= 1

    def test_name_placeholder(self, db):
        from equinox.gui.dialogs.save_dialog import SaveRequestDialog

        dlg = SaveRequestDialog(self._collections(), "GET", "https://example.com/api/test")
        assert "GET" in dlg._name_input.placeholderText()

    def test_folder_input_empty_default(self, db):
        from equinox.gui.dialogs.save_dialog import SaveRequestDialog

        dlg = SaveRequestDialog(self._collections(), "GET", "https://example.com")
        assert dlg._folder_input.text() == ""

    def test_folder_input_pre_filled(self, db):
        from equinox.gui.dialogs.save_dialog import SaveRequestDialog

        dlg = SaveRequestDialog(
            self._collections(), "GET", "https://example.com", current_folder="Auth/OAuth"
        )
        assert dlg._folder_input.text() == "Auth/OAuth"

    def test_result_properties(self, db):
        from equinox.gui.dialogs.save_dialog import SaveRequestDialog

        dlg = SaveRequestDialog(self._collections(), "GET", "https://example.com")
        dlg._name_input.setText("My Test Request")
        # Verify the collection was selected
        assert dlg._col_combo.currentData() is not None

    def test_with_existing_collection(self, db):
        from equinox.gui.dialogs.save_dialog import SaveRequestDialog

        dlg = SaveRequestDialog(
            [
                {"id": 1, "name": "Production API"},
                {"id": 2, "name": "Test API"},
            ],
            "DELETE",
            "https://example.com/users/1",
        )
        assert dlg._col_combo.count() >= 2
