"""Minimal feature-flag helpers for Equinox components.

Centralizes environment-based toggles to reduce scattered reads.
"""

from __future__ import annotations

import os


def is_os_keystore_enabled() -> bool:
    val = os.environ.get("EQUINOX_USE_OS_KEYRING", "0").lower()
    return val in {"1", "true", "yes"}


def is_history_capture_enabled() -> bool:
    # Default to enabled; allow env var to disable
    val = os.environ.get("EQUINOX_HISTORY_CAPTURE_BODIES")
    if val is None:
        return True
    return str(val).lower() in {"1", "true", "yes"}


def is_ssrf_allow_on_dns_failure_enabled() -> bool:
    """Return True when SSRF guard should fail open on DNS failures.

    Secure default is False (fail closed).
    """
    val = os.environ.get("EQUINOX_SSRF_ALLOW_ON_DNS_FAILURE", "0").lower()
    return val in {"1", "true", "yes", "on"}


def is_strict_secret_rotation_enabled() -> bool:
    """Return True when startup secret-rotation errors should abort initialization."""
    val = os.environ.get("EQUINOX_STRICT_SECRET_ROTATION", "0").lower()
    return val in {"1", "true", "yes", "on"}
