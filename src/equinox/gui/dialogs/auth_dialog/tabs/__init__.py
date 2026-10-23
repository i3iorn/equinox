"""Shared per-auth-type form tabs.

These are the single source of truth for authentication forms — both the
request editor's ``AuthDialog`` and the saved-credentials manager embed the
same widget classes, so a form change only ever happens once.
"""

from equinox.gui.dialogs.auth_dialog.tabs.api_key import ApiKeyAuthTab
from equinox.gui.dialogs.auth_dialog.tabs.aws import AwsSigV4AuthTab
from equinox.gui.dialogs.auth_dialog.tabs.base import AuthDialogTab
from equinox.gui.dialogs.auth_dialog.tabs.base import config_text
from equinox.gui.dialogs.auth_dialog.tabs.basic import BasicAuthTab
from equinox.gui.dialogs.auth_dialog.tabs.bearer import BearerAuthTab
from equinox.gui.dialogs.auth_dialog.tabs.oauth2 import OAuth2AuthTab

__all__ = [
    "ApiKeyAuthTab",
    "AuthDialogTab",
    "AwsSigV4AuthTab",
    "BasicAuthTab",
    "BearerAuthTab",
    "OAuth2AuthTab",
    "config_text",
]
