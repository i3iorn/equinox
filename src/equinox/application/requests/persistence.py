"""Request persistence facade for GUI callers.

This module provides a small application-layer boundary around request-related
storage operations so GUI code does not construct storage managers directly.
It intentionally exposes only the operations the request panel needs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Tuple, cast

from equinox.auth import OAuth2Auth
from equinox.core.request import Request
from equinox.storage import CollectionManager, Database

logger = logging.getLogger(__name__)

_FOLDER_AUTH_PREFIX = "folder:"


@dataclass
class SaveRequestResult:
    """Outcome of persisting a request from the save dialog flow."""

    request_id: int
    updated_existing: bool


class RequestPersistenceFacade:
    """Small boundary for request persistence and auth hierarchy operations."""

    def __init__(
        self,
        db: Database,
        collection_manager: Optional[CollectionManager] = None,
    ) -> None:
        self._db = db
        self._collection_manager = collection_manager or CollectionManager(db)

    @property
    def collection_manager(self) -> CollectionManager:
        """Expose the underlying collection manager for transitional callers."""
        return self._collection_manager

    def save_request(self, request: Request, *, collection_id: int, name: str) -> int:
        """Persist a new request into a collection and return its database ID."""
        request_id = self._collection_manager.save_request(
            request,
            collection_id=collection_id,
            name=name,
        )
        return int(request_id)

    def list_save_collections(self) -> list[dict[str, Any]]:
        """Return plain collection choices for the save dialog.

        Creates a default collection for first-time users when the database is
        empty so the dialog always has at least one valid target.
        """
        collections = list(self._collection_manager.list_collections())
        if not collections:
            self._collection_manager.create_collection("My Requests", "Default collection")
            collections = list(self._collection_manager.list_collections())
        return [
            {
                "id": collection["id"],
                "name": collection["name"],
            }
            for collection in collections
        ]

    def update_request(self, request: Request) -> None:
        """Persist edits to an existing request."""
        self._collection_manager.update_request(request)

    def autosave_request(self, request: Request) -> None:
        """Persist autosave edits for an existing request."""
        self._collection_manager.update_request(request)

    def save_request_from_dialog(
        self,
        request: Request,
        *,
        existing_request_id: Optional[int],
        existing_collection_id: Optional[int],
        target_collection_id: int,
        name: str,
    ) -> SaveRequestResult:
        """Persist a dialog-driven save, updating in-place when safe."""
        if existing_request_id and existing_collection_id == target_collection_id:
            request.id = existing_request_id
            self._collection_manager.update_request(request)
            return SaveRequestResult(
                request_id=existing_request_id,
                updated_existing=True,
            )

        new_request_id = self._collection_manager.save_request(
            request,
            collection_id=target_collection_id,
            name=name,
        )
        request.id = new_request_id
        return SaveRequestResult(request_id=new_request_id, updated_existing=False)

    def update_request_auth(self, request_id: int, auth_obj: Any) -> None:
        """Persist auth changes for an existing request."""
        self._collection_manager.update_request_auth(request_id, auth_obj)

    def persist_request_oauth2_token(self, request: Optional[Request], auth_obj: Any) -> bool:
        """Persist refreshed own OAuth2 auth on a saved request row."""
        if (
            request is None
            or not getattr(request, "id", None)
            or not isinstance(auth_obj, OAuth2Auth)
            or not auth_obj.access_token
        ):
            return False
        request_id = getattr(request, "id", None)
        if not isinstance(request_id, int):
            return False
        self._collection_manager.update_request_auth(request_id, auth_obj)
        return True

    def resolve_effective_auth(self, request: Request) -> Tuple[Any, Optional[str]]:
        """Resolve request→folder→collection inherited auth."""
        return cast(
            Tuple[Any, Optional[str]], self._collection_manager.resolve_effective_auth(request)
        )

    def persist_auth_to_source(self, collection_id: int, source: str, auth: Any) -> None:
        """Persist auth back to its owning collection or folder source."""
        if source == "collection":
            self._collection_manager.set_collection_auth(collection_id, auth)
            return
        if source.startswith(_FOLDER_AUTH_PREFIX):
            folder_name = source[len(_FOLDER_AUTH_PREFIX) :]
            self._collection_manager.set_folder_auth(collection_id, folder_name, auth)
            return
        logger.warning(
            "request_persistence.unknown_auth_source op=persist_auth_to_source source=%s",
            source,
        )

    def persist_inherited_oauth2_token(
        self,
        request: Optional[Request],
        source: Optional[str],
        auth_obj: Any,
    ) -> bool:
        """Persist refreshed inherited OAuth2 auth to its owning source."""
        if (
            request is None
            or not getattr(request, "collection_id", None)
            or not source
            or not isinstance(auth_obj, OAuth2Auth)
            or not auth_obj.access_token
        ):
            return False
        collection_id = getattr(request, "collection_id", None)
        if not isinstance(collection_id, int):
            return False
        self.persist_auth_to_source(collection_id, source, auth_obj)
        return True
