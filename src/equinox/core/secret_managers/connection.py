"""Connection testing helpers for secret manager backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from equinox.core.secret_managers.registry import get_secret_manager
from equinox.core.secret_managers.base import SecretAuthError, SecretManagerError


@dataclass
class SecretManagerConnectionResult:
    """Normalized connection test result used by GUI callers."""

    manager_type: str
    ok: bool
    error_kind: Optional[str] = None
    error_message: str = ""


def test_secret_manager_connection(
    manager_type: str,
    config: Dict[str, Any],
) -> SecretManagerConnectionResult:
    """Test a secret manager connection and normalize outcomes."""
    try:
        mgr = get_secret_manager(manager_type)
        mgr.configure(**config)
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

