from __future__ import annotations

from typing import Literal

import pytest
from PyQt6.QtWidgets import QApplication

from equinox.auth import OAuth2Auth
from equinox.gui.dialogs.auth_dialog import AuthDialog


@pytest.fixture(autouse=True)
def ensure_qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _mk_auth(token_auth: Literal["body", "basic"]) -> OAuth2Auth:
    return OAuth2Auth(
        access_token="token-1234",
        token_url="https://plus.dnb.com/v3/token",
        client_id="client-id",
        client_secret="client-secret",
        token_auth=token_auth,
    )


def test_oauth_fetch_status_shows_fallback_hint_and_updates_client_auth_mode() -> None:
    dlg = AuthDialog()
    dlg.tabs.setCurrentIndex(dlg._TAB_OAUTH2)
    dlg.oauth2_token_auth.setCurrentIndex(dlg.oauth2_token_auth.findData("body"))
    dlg._fetch_requested_token_auth = "body"

    dlg._on_token_fetched({"ok": True, "auth": _mk_auth("basic"), "response": None})

    assert dlg.oauth2_token_auth.currentData() == "basic"
    assert "Hint:" in dlg.oauth2_fetch_status.text()
    assert "click Save to persist" in dlg.oauth2_fetch_status.text()


def test_oauth_fetch_status_omits_hint_when_mode_does_not_change() -> None:
    dlg = AuthDialog()
    dlg.tabs.setCurrentIndex(dlg._TAB_OAUTH2)
    dlg.oauth2_token_auth.setCurrentIndex(dlg.oauth2_token_auth.findData("body"))
    dlg._fetch_requested_token_auth = "body"

    dlg._on_token_fetched({"ok": True, "auth": _mk_auth("body"), "response": None})

    assert dlg.oauth2_token_auth.currentData() == "body"
    assert "Hint:" not in dlg.oauth2_fetch_status.text()
