"""Normalized secret-manager profile helpers.

This module centralizes the shape of secret-manager GUI/storage profiles so
widgets, storage, and connection helpers do not each re-implement the same
payload parsing rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from equinox.core.secret_managers.base import SecretManager
from equinox.core.secret_managers.registry import get_secret_manager

_DEFAULT_CACHE_TTL = 300
_DEFAULT_MANAGER_TYPE = "env"


@dataclass(frozen=True)
class SecretManagerProfile:
    """Normalized secret-manager configuration profile."""

    manager_type: str
    config: dict[str, Any]
    enable_cache: bool = True
    cache_ttl: int = _DEFAULT_CACHE_TTL

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SecretManagerProfile:
        """Build a normalized profile from a mapping-like payload."""
        manager_type = (
            str(payload.get("type") or _DEFAULT_MANAGER_TYPE).strip() or _DEFAULT_MANAGER_TYPE
        )
        raw_config = payload.get("config", {})
        config = dict(raw_config) if isinstance(raw_config, Mapping) else {}

        try:
            cache_ttl = max(0, int(payload.get("cache_ttl", _DEFAULT_CACHE_TTL)))
        except (TypeError, ValueError):
            cache_ttl = _DEFAULT_CACHE_TTL

        return cls(
            manager_type=manager_type,
            config=config,
            enable_cache=bool(payload.get("enable_cache", True)),
            cache_ttl=cache_ttl,
        )

    @classmethod
    def from_manager_config(
        cls,
        manager_type: str,
        config: Mapping[str, Any],
        enable_cache: bool = True,
        cache_ttl: int = _DEFAULT_CACHE_TTL,
    ) -> SecretManagerProfile:
        """Build a normalized profile from discrete config values."""
        return cls.from_payload(
            {
                "type": manager_type,
                "config": dict(config),
                "enable_cache": enable_cache,
                "cache_ttl": cache_ttl,
            },
        )

    def to_payload(self) -> dict[str, Any]:
        """Serialize the profile back to the storage/UI payload shape."""
        return {
            "type": self.manager_type,
            "config": dict(self.config),
            "enable_cache": self.enable_cache,
            "cache_ttl": self.cache_ttl,
        }

    def get_manager(self) -> SecretManager:
        """Return a configured secret-manager instance for this profile."""
        manager = get_secret_manager(
            self.manager_type,
            enable_cache=self.enable_cache,
            cache_ttl=self.cache_ttl,
        )
        manager.configure(**self.config)
        return manager
