"""Shared GUI feedback helpers for secret-manager operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtWidgets import QMessageBox, QWidget

from equinox.core.secret_managers import SecretManagerConnectionResult


@dataclass(frozen=True)
class SecretManagerConnectionMessages:
    """Context-specific message templates for connection-test outcomes."""

    success: str
    unavailable: str
    auth: str
    config: str
    unexpected: str


def show_secret_manager_connection_feedback(
    parent: Optional[QWidget],
    result: SecretManagerConnectionResult,
    messages: SecretManagerConnectionMessages,
) -> None:
    """Display a normalized connection-test result using caller-specific copy."""
    values = {
        "manager_type": result.manager_type,
        "error": result.error_message,
    }
    if result.ok:
        QMessageBox.information(parent, "Connection Successful", messages.success.format(**values))
        return

    if result.error_kind == "unavailable":
        QMessageBox.warning(parent, "Connection Failed", messages.unavailable.format(**values))
        return

    if result.error_kind == "auth":
        QMessageBox.critical(parent, "Authentication Error", messages.auth.format(**values))
        return

    if result.error_kind == "config":
        QMessageBox.critical(parent, "Configuration Error", messages.config.format(**values))
        return

    QMessageBox.critical(parent, "Error", messages.unexpected.format(**values))

