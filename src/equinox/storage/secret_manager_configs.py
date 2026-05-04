"""Persistent storage for secret manager UI configurations.

This module owns serialization/deserialization for secret manager connection
profiles used by GUI components. Keeping this in ``storage/`` avoids embedding
file I/O concerns in widgets.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from equinox.core.secret_managers import SecretManagerProfile

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path.home() / ".equinox" / "secret_managers.json"


class SecretManagerConfigStore:
    """Read/write named secret manager configurations to a JSON file."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.config_path = config_path or _DEFAULT_CONFIG_PATH
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        """Load all saved configurations.

        Returns an empty dict on missing file or invalid payload.
        """
        if not self.config_path.exists():
            return {}

        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to read secret manager configurations: %s", exc)
            return {}

        if not isinstance(raw, dict):
            logger.warning("Ignoring invalid secret manager configuration payload")
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        for name, payload in raw.items():
            if not isinstance(name, str) or not isinstance(payload, dict):
                continue
            out[name] = SecretManagerProfile.from_payload(payload).to_payload()
        return out

    def save_all(self, configs: Dict[str, Dict[str, Any]]) -> None:
        """Persist *configs* to disk atomically."""
        serializable = {
            str(name): SecretManagerProfile.from_payload(payload).to_payload()
            for name, payload in configs.items()
            if isinstance(payload, dict)
        }
        tmp_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        tmp_path.replace(self.config_path)

    def upsert(self, name: str, payload: Dict[str, Any]) -> None:
        """Create or update a single named configuration."""
        configs = self.load_all()
        configs[name] = payload
        self.save_all(configs)

    def delete(self, name: str) -> None:
        """Delete a named configuration if it exists."""
        configs = self.load_all()
        configs.pop(name, None)
        self.save_all(configs)

