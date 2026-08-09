"""Shared GUI feedback helpers for secret-manager operations."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import QWidget

from equinox.core.secret_managers import SecretManagerConnectionResult
from equinox.gui.error_presenter import ErrorPresenter


@dataclass(frozen=True)
class SecretManagerConnectionMessages:
    """Context-specific message templates for connection-test outcomes."""

    success: str
    unavailable: str
    auth: str
    config: str
    unexpected: str


def show_secret_manager_connection_feedback(
    parent: QWidget | None,
    result: SecretManagerConnectionResult,
    messages: SecretManagerConnectionMessages,
) -> None:
    """Display a normalized connection-test result using caller-specific copy."""
    values = {
        "manager_type": result.manager_type,
        "error": result.error_message,
    }
    if result.ok:
        ErrorPresenter.info(
            parent,
            messages.success.format(**values),
            title="Connection Successful",
        )
        return

    if result.error_kind == "unavailable":
        ErrorPresenter.warning(
            parent,
            messages.unavailable.format(**values),
            title="Connection Failed",
        )
        return

    if result.error_kind == "auth":
        ErrorPresenter.error(parent, messages.auth.format(**values), title="Authentication Error")
        return

    if result.error_kind == "config":
        ErrorPresenter.error(parent, messages.config.format(**values), title="Configuration Error")
        return

    ErrorPresenter.error(parent, messages.unexpected.format(**values), title="Error")
