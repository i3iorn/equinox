from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox

from equinox.gui.dialogs.secret_manager_config_dialog import SecretManagerConfigDialog
from equinox.gui.secret_manager_panel import SecretManagerSettingsPanel


def test_vault_dialog_shows_warning_for_insecure_http_override(qtbot) -> None:
    dialog = SecretManagerConfigDialog()
    qtbot.addWidget(dialog)

    index = dialog.type_combo.findText("vault")
    if index >= 0:
        dialog.type_combo.setCurrentIndex(index)
    else:
        dialog.type_combo.setCurrentText("hashicorp_vault")

    url_input = dialog._config_widgets["url"][0]
    allow_checkbox = dialog._config_widgets["allow_insecure_http"][0]

    url_input.setText("http://vault.local:8200")
    allow_checkbox.setChecked(True)
    dialog._update_vault_security_warning()

    assert dialog._vault_warning_label is not None
    assert not dialog._vault_warning_label.isHidden()
    assert "insecure Vault HTTP override" in dialog._vault_warning_label.text()

    config = dialog._get_config_dict()
    assert config["allow_insecure_http"] is True


def test_vault_dialog_requires_confirmation_for_insecure_http_override(qtbot, monkeypatch) -> None:
    dialog = SecretManagerConfigDialog()
    qtbot.addWidget(dialog)

    index = dialog.type_combo.findText("vault")
    if index >= 0:
        dialog.type_combo.setCurrentIndex(index)
    else:
        dialog.type_combo.setCurrentText("hashicorp_vault")

    url_input = dialog._config_widgets["url"][0]
    allow_checkbox = dialog._config_widgets["allow_insecure_http"][0]
    url_input.setText("http://vault.local:8200")
    allow_checkbox.setChecked(True)

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )

    assert dialog._confirm_insecure_vault_http("vault", dialog._get_config_dict(), "Save") is False


def test_secret_manager_panel_redacts_sensitive_config_and_shows_http_warning(
    qtbot, tmp_path
) -> None:
    panel = SecretManagerSettingsPanel(config_path=tmp_path / "secret_managers.json")
    qtbot.addWidget(panel)

    panel._current_config = {
        "type": "vault",
        "config": {
            "url": "http://vault.local:8200",
            "token": "super-secret-token",
            "allow_insecure_http": True,
        },
        "enable_cache": True,
        "cache_ttl": 300,
    }
    panel._display_config()
    text = panel.config_display.toPlainText()

    assert "WARNING: insecure Vault HTTP override is enabled" in text
    assert "super-secret-token" not in text
    assert "[REDACTED]" in text
