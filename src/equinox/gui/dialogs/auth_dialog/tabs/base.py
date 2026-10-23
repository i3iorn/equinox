"""Base class for the shared per-auth-type tab widgets.

These tabs are the single source of truth for authentication forms:
:class:`~equinox.gui.dialogs.auth_dialog.AuthDialog` (the request editor's
"Configure Authentication" dialog) and
:class:`~equinox.gui.dialogs.saved_credentials_dialog.SavedCredentialsDialog`
(Environment → Manage Saved Credentials) both embed the very same widget
instances, so a field added here appears in — and is collected from — both
places with no duplicated form code.
"""

from collections.abc import Callable
from typing import Any

from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtWidgets import QComboBox
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtWidgets import QWidget


def config_text(config: dict[str, Any], key: str, default: str = "") -> str:
    """Read a config value as text, treating ``None`` as the default.

    Optional fields are persisted as ``None`` rather than omitted, and
    ``QLineEdit.setText(None)`` raises, so ``None`` must fall back too.
    """
    value = config.get(key)
    return default if value is None else str(value)


class AuthDialogTab(QWidget):
    """Base class for the shared authentication form tabs."""

    def get_auth_config(self) -> dict[str, Any]:
        """Return the authentication configuration as a dictionary."""
        raise NotImplementedError("Subclasses must implement get_auth_config()")

    def set_auth_config(self, config: dict[str, Any]) -> None:
        """Populate the form from a saved-credential style config dict."""
        raise NotImplementedError("Subclasses must implement set_auth_config()")

    def validate(self) -> str | None:
        """Return an error message when required fields are missing/invalid.

        ``None`` means the configuration collected by :meth:`get_auth_config`
        is complete.  The message is shown verbatim by every host dialog, so
        validation stays identical wherever the tab is embedded.
        """
        return None

    def clear(self) -> None:
        """Reset every field to its construction-time default."""
        self.set_auth_config({})

    def iter_form_widgets(self) -> list[QWidget]:
        """Return the editable form widgets owned by this tab.

        Buttons and status labels are excluded — hosts decide what happens on
        click and render their own status text.
        """
        widgets: list[QWidget] = []
        for cls in (QLineEdit, QTextEdit, QComboBox, QCheckBox):
            widgets.extend(self.findChildren(cls))
        return widgets

    def connect_form_changed(self, callback: Callable[[], None]) -> None:
        """Invoke ``callback`` whenever any form field changes.

        Lets host dialogs wire their dirty-tracking once instead of reaching
        into every individual field.
        """
        for widget in self.iter_form_widgets():
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(callback)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(callback)
            elif isinstance(widget, (QLineEdit, QTextEdit)):
                widget.textChanged.connect(callback)
