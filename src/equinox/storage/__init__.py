"""Storage and database management"""

import os
from pathlib import Path

from equinox.storage.collections import CollectionManager
from equinox.storage.database import Database
from equinox.storage.environments import EnvironmentManager
from equinox.storage.global_variables import GlobalVariablesManager
from equinox.storage.history import HistoryManager
from equinox.storage.migrations import MIGRATIONS, MigrationRunner
from equinox.storage.oauth_clients import OAuthClientManager
from equinox.storage.saved_credentials import SavedCredentialsManager
from equinox.storage.secret_manager_configs import SecretManagerConfigStore
from equinox.storage.variable_groups import VariableGroupManager

# Canonical database path shared by CLI and GUI
_DEFAULT_DB_PATH = Path.home() / ".equinox" / "equinox.db"


def get_db() -> Database:
    """Return a Database instance pointing at the Equinox database.

    Respects the ``EQUINOX_DB_PATH`` environment variable if set,
    otherwise uses ``~/.equinox/equinox.db``.
    """
    custom = os.environ.get("EQUINOX_DB_PATH")
    if custom:
        db_path = Path(custom)
    else:
        db_path = _DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return Database(str(db_path))


__all__ = [
    "Database",
    "CollectionManager",
    "EnvironmentManager",
    "HistoryManager",
    "VariableGroupManager",
    "GlobalVariablesManager",
    "OAuthClientManager",
    "SavedCredentialsManager",
    "SecretManagerConfigStore",
    "MigrationRunner",
    "MIGRATIONS",
    "get_db",
]
