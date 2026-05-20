"""Connection testing helpers for secret manager backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from equinox.core.secret_managers.base import SecretAuthError, SecretManagerError
from equinox.core.secret_managers.profiles import SecretManagerProfile


@dataclass
class SecretManagerConnectionResult:
    """Normalized connection test result used by GUI callers."""

    manager_type: str
    ok: bool
    error_kind: str | None = None
    error_message: str = ""


def test_secret_manager_connection(
    manager_type: str,
    config: dict[str, Any],
) -> SecretManagerConnectionResult:
    """Test a secret manager connection and normalize outcomes."""
    try:
        enable_cache = bool(config.get("enable_cache", True))
        try:
            cache_ttl = int(config.get("cache_ttl", 300))
        except (TypeError, ValueError):
            cache_ttl = 300
        manager_config = {
            key: value for key, value in config.items() if key not in {"enable_cache", "cache_ttl"}
        }
        mgr = SecretManagerProfile.from_manager_config(
            manager_type,
            manager_config,
            enable_cache=enable_cache,
            cache_ttl=cache_ttl,
        ).get_manager()
        if mgr.is_available():
            return SecretManagerConnectionResult(manager_type=manager_type, ok=True)
        return SecretManagerConnectionResult(
            manager_type=manager_type,
            ok=False,
            error_kind="unavailable",
            error_message="Manager reported unavailable after configuration.",
        )
    except SecretAuthError as exc:
        return SecretManagerConnectionResult(
            manager_type=manager_type,
            ok=False,
            error_kind="auth",
            error_message=str(exc),
        )
    except SecretManagerError as exc:
        return SecretManagerConnectionResult(
            manager_type=manager_type,
            ok=False,
            error_kind="config",
            error_message=str(exc),
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        return SecretManagerConnectionResult(
            manager_type=manager_type,
            ok=False,
            error_kind="unexpected",
            error_message=str(exc),
        )
