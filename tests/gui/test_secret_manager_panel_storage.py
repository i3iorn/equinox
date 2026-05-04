from __future__ import annotations

import json
from pathlib import Path

from equinox.gui.secret_manager_panel import SecretManagerSettingsPanel


def test_secret_manager_panel_persists_created_profile(qtbot, tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "secret_managers.json"
    panel = SecretManagerSettingsPanel(config_path=config_path)
    qtbot.addWidget(panel)

    monkeypatch.setattr(
        "PyQt6.QtWidgets.QInputDialog.getText",
        lambda *args, **kwargs: ("Dev Vault", True),
    )

    panel._on_config_created(
        "vault",
        {
            "url": "https://vault.example.com:8200",
            "token": "token-value",
            "enable_cache": False,
            "cache_ttl": 42,
        },
    )

    assert config_path.exists()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert "Dev Vault" in raw
    assert raw["Dev Vault"]["type"] == "vault"
    assert raw["Dev Vault"]["cache_ttl"] == 42


def test_secret_manager_panel_loads_profiles_from_storage(qtbot, tmp_path: Path) -> None:
    config_path = tmp_path / "secret_managers.json"
    config_path.write_text(
        json.dumps(
            {
                "Local Env": {
                    "type": "env",
                    "config": {"prefix": "EQUINOX_SECRET_"},
                    "enable_cache": True,
                    "cache_ttl": 300,
                }
            }
        ),
        encoding="utf-8",
    )

    panel = SecretManagerSettingsPanel(config_path=config_path)
    qtbot.addWidget(panel)

    assert panel.config_combo.count() == 1
    assert panel.config_combo.currentText() == "Local Env"
    assert panel._current_config.get("type") == "env"
    assert "Manager Type: env" in panel.config_display.toPlainText()

