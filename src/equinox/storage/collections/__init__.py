"""Collections package — CRUD for collections, requests, folders, auth, and variables.

Sub-modules
-----------
- ``manager``    – :class:`CollectionManager` and param serialisation helpers
- ``auth``       – Auth serialisation / hierarchy-resolution mixin
- ``folders``    – Folder CRUD mixin
- ``ordering``   – Request ordering and move mixin
- ``variables``  – Collection-variable and variable-group mixin
"""

from equinox.storage.collections.manager import CollectionManager  # noqa: F401 – public API
from equinox.storage.collections.auth import CollectionAuthMixin  # noqa: F401
from equinox.storage.collections.folders import CollectionFoldersMixin  # noqa: F401
from equinox.storage.collections.ordering import CollectionOrderingMixin  # noqa: F401
from equinox.storage.collections.variables import CollectionVariablesMixin  # noqa: F401

__all__ = [
    "CollectionManager",
    "CollectionAuthMixin",
    "CollectionFoldersMixin",
    "CollectionOrderingMixin",
    "CollectionVariablesMixin",
]

