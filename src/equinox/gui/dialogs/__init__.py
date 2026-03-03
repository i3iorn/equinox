"""Modal dialog windows for Equinox GUI.

Re-exports for backward compatibility — consumers may import directly
from ``equinox.gui.dialogs`` or from the individual sub-modules.
"""

from equinox.gui.dialogs.auth_dialog import AuthDialog
from equinox.gui.dialogs.environment_dialog import EnvironmentDialog
from equinox.gui.dialogs.saved_credentials_dialog import SavedCredentialsDialog
from equinox.gui.dialogs.preferences_dialog import PreferencesDialog
from equinox.gui.dialogs.collection_variables_dialog import (
    CollectionVariablesDialog,
    AddVariableGroupDialog,
)

__all__ = [
    "AuthDialog",
    "EnvironmentDialog",
    "SavedCredentialsDialog",
    "PreferencesDialog",
    "CollectionVariablesDialog",
    "AddVariableGroupDialog",
]

