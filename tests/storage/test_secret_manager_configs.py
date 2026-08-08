from __future__ import annotations

import json
from pathlib import Path

from equinox.storage.secret_manager_configs import SecretManagerConfigStore


def test_secret_manager_config_store_round_trip(tmp_path: Path) -> None:
    store = SecretManagerConfigStore(tmp_path / "secret_managers.json")
    payload = {
        "dev-vault": {
            "type": "vault",
            "config": {
                "url": "https://vault.example.com:8200",
                "token": "token-value",
                "allow_insecure_http": False,
            },
            "enable_cache": True,
            "cache_ttl": 120,
        },
    }

    store.save_all(payload)
    loaded = store.load_all()

    assert "dev-vault" in loaded
    assert loaded["dev-vault"]["type"] == "vault"
    assert loaded["dev-vault"]["config"]["url"].startswith("https://")
    assert loaded["dev-vault"]["cache_ttl"] == 120


def test_secret_manager_config_store_discards_invalid_payload(tmp_path: Path) -> None:
    p = tmp_path / "secret_managers.json"
    p.write_text(json.dumps(["bad", "payload"]), encoding="utf-8")

    store = SecretManagerConfigStore(p)

    assert store.load_all() == {}


def test_secret_manager_config_store_upsert_and_delete(tmp_path: Path) -> None:
    store = SecretManagerConfigStore(tmp_path / "secret_managers.json")

    store.upsert(
        "local-env",
        {
            "type": "env",
            "config": {"prefix": "EQUINOX_SECRET_"},
            "enable_cache": True,
            "cache_ttl": 300,
        },
    )
    assert "local-env" in store.load_all()

    store.delete("local-env")
    assert "local-env" not in store.load_all()
