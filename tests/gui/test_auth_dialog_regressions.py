"""Regression tests for the auth dialog and saved-credentials manager.

Each test here covers a defect that was silent in the GUI: the dialogs still
opened, but credentials were dropped, never persisted, or the form stayed
disabled.  Unit coverage existed for the storage layer, so nothing failed.
"""

from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QMessageBox

from equinox.auth import APIKeyAuth, AWSSigV4Auth, BasicAuth, BearerAuth, OAuth2Auth
from .gui_helpers import process as _process


# ─────────────────────────────────────────────────────────────────────────────
# AuthDialog — loading an existing strategy into the form
# ─────────────────────────────────────────────────────────────────────────────


class TestAuthDialogLoadsCurrentAuth:
    """Opening the dialog must show the auth the caller passed in.

    Regression: ``current_auth`` was stored but never applied to the tabs, so
    an already-configured request opened blank on "No Auth" and saving
    discarded the credentials.
    """

    @pytest.mark.parametrize(
        ("auth", "tab_title"),
        [
            (BasicAuth(username="alice", password="pw"), "Basic Auth"),  # pragma: allowlist secret
            (BearerAuth(token="tok-123"), "Bearer Token"),
            (
                OAuth2Auth(token_url="https://a.example/token", client_id="cid"),
                "OAuth 2.0",
            ),
            (APIKeyAuth(key="X-API-Key", value="v1"), "API Key"),
            (
                AWSSigV4Auth(
                    access_key="AKIA",
                    secret_key="sk",  # pragma: allowlist secret
                    region="eu-west-1",
                    service="execute-api",
                ),
                "AWS SigV4",
            ),
        ],
    )
    def test_selects_matching_tab(self, qapp, auth, tab_title):
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(auth, None)
        assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == tab_title

    def test_no_auth_selects_no_auth_tab(self, qapp):
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(None, None)
        assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == "No Auth"

    def test_basic_fields_populated(self, qapp):
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(BasicAuth(username="alice", password="pw"), None)
        assert dialog.basic.username.text() == "alice"
        assert dialog.basic.password.text() == "pw"

    def test_oauth2_fields_populated(self, qapp):
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        auth = OAuth2Auth(
            token_url="https://a.example/token",
            client_id="cid",
            client_secret="csec",  # pragma: allowlist secret
            scope="read write",
            access_token="at-1",
            refresh_token="rt-1",
            token_auth="basic",
        )
        dialog = AuthDialog(auth, None)

        assert dialog.oauth2.token_url.text() == "https://a.example/token"
        assert dialog.oauth2.client_id.text() == "cid"
        assert dialog.oauth2.client_secret.text() == "csec"
        assert dialog.oauth2.scope.text() == "read write"
        assert dialog.oauth2.access_token.text() == "at-1"
        assert dialog.oauth2.refresh_token.text() == "rt-1"
        assert dialog.oauth2.token_auth.currentData() == "basic"

    def test_api_key_query_location_populated(self, qapp):
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(APIKeyAuth(key="k", value="v", location="query"), None)
        assert dialog.api_key.location.currentText() == "query"

    def test_aws_fields_populated(self, qapp):
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        auth = AWSSigV4Auth(
            access_key="AKIA",
            secret_key="sk",  # pragma: allowlist secret
            region="eu-west-1",
            service="execute-api",
            session_token="st",
        )
        dialog = AuthDialog(auth, None)

        assert dialog.aws.access_key.text() == "AKIA"
        assert dialog.aws.region.text() == "eu-west-1"
        assert dialog.aws.service.text() == "execute-api"
        assert dialog.aws.session_token.text() == "st"


# ─────────────────────────────────────────────────────────────────────────────
# AuthDialog — saving back an auth strategy
# ─────────────────────────────────────────────────────────────────────────────


class TestAuthDialogSavesStrategy:
    """Saving must expose an ``AuthStrategy``, not a raw config dict.

    Regression: ``_save_auth`` emitted ``tab.get_auth_config()`` and never set
    ``_saved_auth``.  Every caller reads ``dialog._saved_auth``, so configuring
    auth was a silent no-op that also cleared any existing auth.
    """

    @pytest.mark.parametrize(
        "auth",
        [
            BasicAuth(username="alice", password="pw"),  # pragma: allowlist secret
            BearerAuth(token="tok-123"),
            OAuth2Auth(
                token_url="https://a.example/token",
                client_id="cid",
                client_secret="csec",  # pragma: allowlist secret
                scope="read",
                access_token="at-1",
                refresh_token="rt-1",
            ),
            APIKeyAuth(key="X-API-Key", value="v1", location="query"),
            AWSSigV4Auth(
                access_key="AKIA",
                secret_key="sk",  # pragma: allowlist secret
                region="eu-west-1",
                service="execute-api",
            ),
        ],
    )
    def test_round_trips_unchanged(self, qapp, auth):
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(auth, None)
        rebuilt = dialog._build_auth_from_tab()

        assert type(rebuilt) is type(auth)
        for field, before in vars(auth).items():
            if field.startswith("_"):
                continue
            assert getattr(rebuilt, field) == before, field

    def test_saved_auth_is_set_after_save(self, qapp):
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(BearerAuth(token="abc"), None)
        dialog._save_auth()

        assert isinstance(dialog._saved_auth, BearerAuth)
        assert dialog._saved_auth.token == "abc"

    def test_auth_configured_emits_a_strategy(self, qapp):
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(BearerAuth(token="abc"), None)
        received = []
        dialog.auth_configured.connect(received.append)
        dialog._save_auth()

        assert len(received) == 1
        assert isinstance(received[0], BearerAuth)

    def test_no_auth_tab_builds_none(self, qapp):
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(None, None)
        assert dialog._build_auth_from_tab() is None

    def test_missing_required_field_blocks_save(self, qapp):
        """An incomplete form must warn instead of accepting the dialog."""
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(BearerAuth(token="tok"), None)
        dialog.bearer.token.clear()

        with patch.object(QMessageBox, "warning") as warn:
            dialog._save_auth()

        assert warn.called
        assert dialog._saved_auth is None

    def test_pasted_newlines_are_stripped(self, qapp):
        """Password managers paste trailing newlines; those must not persist."""
        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(BearerAuth(token="tok"), None)
        dialog.bearer.token.setText("tok-with-newline\n")

        rebuilt = dialog._build_auth_from_tab()
        assert rebuilt.token == "tok-with-newline"


class TestAuthDialogTokenExpiry:
    """A freshly fetched OAuth2 token must keep its expiry.

    Regression: the fetched strategy was discarded, so the saved token had no
    ``expires_at`` and was treated as valid forever.
    """

    def test_expires_at_carried_forward(self, qapp):
        from datetime import datetime, timedelta, timezone

        from equinox.gui.dialogs.auth_dialog import AuthDialog

        expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        dialog = AuthDialog(
            OAuth2Auth(token_url="https://a.example/token", client_id="cid"),
            None,
        )

        fetched = OAuth2Auth(
            token_url="https://a.example/token",
            client_id="cid",
            access_token="fresh-token",
        )
        fetched.expires_at = expiry
        dialog.oauth2_controller._last_fetched_auth = fetched
        dialog.oauth2.access_token.setText("fresh-token")

        rebuilt = dialog._build_auth_from_tab()
        assert rebuilt.expires_at == expiry

    def test_expiry_not_applied_to_a_different_token(self, qapp):
        """A hand-typed token must not inherit the fetched token's expiry."""
        from datetime import datetime, timedelta, timezone

        from equinox.gui.dialogs.auth_dialog import AuthDialog

        dialog = AuthDialog(
            OAuth2Auth(token_url="https://a.example/token", client_id="cid"),
            None,
        )

        fetched = OAuth2Auth(
            token_url="https://a.example/token",
            client_id="cid",
            access_token="fetched-token",
        )
        fetched.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        dialog.oauth2_controller._last_fetched_auth = fetched
        dialog.oauth2.access_token.setText("hand-typed-token")

        rebuilt = dialog._build_auth_from_tab()
        assert rebuilt.expires_at is None


# ─────────────────────────────────────────────────────────────────────────────
# SavedCredentialsDialog
# ─────────────────────────────────────────────────────────────────────────────


class TestSavedCredentialsDialogBehaviour:
    """The credential manager must be usable, not just constructible.

    Regressions: the service never received a config collector (so Save always
    failed), the form was disabled at build time and never re-enabled, the
    dirty flag was never cleared, and ``credentials_changed`` never fired.
    """

    @pytest.fixture()
    def seeded(self, db):
        from equinox.storage.saved_credentials import SavedCredentialsManager

        mgr = SavedCredentialsManager(db)
        mgr.create(
            "OAuth no-scope",
            "oauth2",
            {
                "token_url": "https://a.example/token",
                "client_id": "cid",
                "client_secret": None,
                "scope": None,
            },
        )
        mgr.create("My Bearer", "bearer", {"token": "tok"})
        mgr.create(
            "My AWS",
            "aws_sigv4",
            {
                "access_key": "AKIA",
                "secret_key": "sk",  # pragma: allowlist secret
                "region": "eu-west-1",
                "service": "execute-api",
            },
        )
        return mgr

    @pytest.fixture()
    def dialog(self, qapp, db, seeded):
        from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog

        return SavedCredentialsDialog(db)

    @staticmethod
    def _select(dialog, name):
        """Select a credential by name; list order is auth_type-then-name."""
        for row in range(dialog.cred_list.count()):
            if name in dialog.cred_list.item(row).text():
                dialog.cred_list.setCurrentRow(row)
                _process()
                return
        raise AssertionError(f"no credential named {name!r} in the list")

    def test_form_disabled_until_a_credential_is_selected(self, dialog):
        assert dialog.cred_list.count() == 3
        assert not dialog.f_name.isEnabled()

    def test_selecting_a_credential_enables_and_fills_the_form(self, dialog):
        self._select(dialog, "OAuth no-scope")

        assert dialog.f_name.isEnabled()
        assert dialog.f_name.text() == "OAuth no-scope"
        assert dialog.o_token_url.text() == "https://a.example/token"

    def test_null_optional_fields_load_as_empty_text(self, dialog):
        """``scope``/``client_secret`` persist as None; setText(None) raises."""
        self._select(dialog, "OAuth no-scope")

        assert dialog.o_scope.text() == ""
        assert dialog.o_client_secret.text() == ""

    def test_form_is_clean_immediately_after_loading(self, dialog):
        self._select(dialog, "OAuth no-scope")

        assert dialog._dirty is False
        assert "*" not in dialog.save_btn.text()

    def test_editing_marks_the_form_dirty(self, dialog):
        self._select(dialog, "My AWS")
        dialog.aws_service.setText("s3")

        assert dialog._dirty is True

    @pytest.mark.parametrize(
        ("name", "expected_page"),
        [("OAuth no-scope", 0), ("My Bearer", 3), ("My AWS", 4)],
    )
    def test_credential_type_selects_the_matching_form_page(
        self,
        dialog,
        name,
        expected_page,
    ):
        self._select(dialog, name)

        assert dialog.stack.currentIndex() == expected_page

    def test_save_persists_the_edit(self, dialog, seeded):
        self._select(dialog, "My AWS")
        dialog.aws_service.setText("s3")

        assert dialog._controller._on_save() is True

        stored = next(c for c in seeded.list() if c["name"] == "My AWS")
        assert seeded.get(stored["id"])["config"]["service"] == "s3"

    def test_save_clears_the_dirty_flag(self, dialog):
        self._select(dialog, "My AWS")
        dialog.aws_service.setText("s3")
        dialog._controller._on_save()

        assert dialog._dirty is False

    def test_save_emits_credentials_changed(self, dialog):
        """AuthDialog refreshes its picker off this signal."""
        self._select(dialog, "My AWS")
        dialog.aws_service.setText("s3")

        received = []
        dialog.credentials_changed.connect(lambda: received.append(1))
        dialog._controller._on_save()

        assert received == [1]

    def test_save_reports_validation_errors(self, dialog):
        """A blanked required field must surface an error, not save silently."""
        self._select(dialog, "OAuth no-scope")
        dialog.o_token_url.clear()

        with patch.object(QMessageBox, "warning") as warn:
            assert dialog._controller._on_save() is False

        assert warn.called

    def test_delete_clears_and_disables_the_form(self, dialog):
        self._select(dialog, "My Bearer")
        before = dialog.cred_list.count()

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dialog._controller._on_delete()
        _process()

        assert dialog.cred_list.count() == before - 1
        assert dialog.f_name.text() == ""
        assert not dialog.f_name.isEnabled()

    def test_set_default_does_not_run_on_an_invalid_form(self, dialog, seeded):
        """Promoting an unsaved, invalid form must not change the default."""
        self._select(dialog, "OAuth no-scope")
        dialog.o_client_id.clear()

        with patch.object(QMessageBox, "warning"):
            dialog._controller._on_set_default()

        assert not any(c["is_default"] for c in seeded.list())


class TestOAuthTestResponseDialog:
    """The shared "View Response…" path must resolve its dialog import.

    Regression: the mixin imported ``_TokenResponseDialog`` from
    ``auth_dialog``, a name that disappeared when that module became a package.
    """

    def test_token_response_dialog_is_importable(self):
        from equinox.gui.dialogs.auth_dialog.oauth2.response_dialog import (
            OAuth2TokenResponseDialog,
        )

        assert OAuth2TokenResponseDialog is not None

    def test_open_token_response_shows_the_dialog(self, qapp, db):
        from equinox.gui.dialogs.oauth_clients_dialog import OAuthClientsDialog

        dialog = OAuthClientsDialog(db)
        dialog._last_test_response = {"access_token": "secret", "expires_in": 3600}

        with patch(
            "equinox.gui.dialogs.auth_dialog.oauth2.response_dialog.OAuth2TokenResponseDialog.exec",
        ) as shown:
            dialog._view_test_response()

        assert shown.called
