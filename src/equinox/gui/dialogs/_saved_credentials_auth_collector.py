"""Auth-config collection for the saved credentials dialog.

The saved-credentials manager embeds the very same per-type tab widgets as
the request editor's ``AuthDialog``, so collection/validation simply
delegates to them — there is exactly one implementation of each form and
one of its validation rules (see ``auth_dialog/tabs/``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab


@dataclass(frozen=True)
class AuthConfigResult:
    config: dict[str, Any] | None
    error: str | None


class AuthConfigCollector:
    """Collects and validates authentication configuration from the tabs."""

    def __init__(self, tabs: Mapping[str, AuthDialogTab]) -> None:
        self._tabs: dict[str, AuthDialogTab] = dict(tabs)

    def collect(self, auth_type: str) -> AuthConfigResult:
        tab = self._tabs.get(auth_type)
        if tab is None:
            return AuthConfigResult(None, f"Unsupported authentication type: {auth_type}")
        error = tab.validate()
        if error is not None:
            return AuthConfigResult(None, error)
        return AuthConfigResult(dict(tab.get_auth_config()), None)
