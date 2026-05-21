from __future__ import annotations

from unittest.mock import Mock

from equinox.application.requests import RequestPersistenceFacade
from equinox.auth import OAuth2Auth
from equinox.core.request import Request


def test_request_persistence_facade_delegates_request_operations() -> None:
    manager = Mock()
    manager.save_request.return_value = 33
    facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)
    request = Request(method="GET", url="https://example.com")

    facade.save_request(request, collection_id=7, name="Demo")
    facade.update_request(request)
    facade.autosave_request(request)
    facade.update_request_auth(99, {"token": "abc"})

    manager.save_request.assert_called_once_with(request, collection_id=7, name="Demo")
    manager.update_request.assert_any_call(request)
    assert manager.update_request.call_count == 2
    manager.update_request_auth.assert_called_once_with(99, {"token": "abc"})


def test_request_persistence_facade_lists_save_collections_and_creates_default() -> None:
    manager = Mock()
    manager.list_collections.side_effect = [[], [{"id": 1, "name": "My Requests"}]]
    facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)

    result = facade.list_save_collections()

    assert result == [{"id": 1, "name": "My Requests"}]
    manager.create_collection.assert_called_once_with("My Requests", "Default collection")


def test_request_persistence_facade_save_request_from_dialog_updates_existing() -> None:
    manager = Mock()
    facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)
    request = Request(method="GET", url="https://example.com")

    result = facade.save_request_from_dialog(
        request,
        existing_request_id=11,
        existing_collection_id=7,
        target_collection_id=7,
        name="Demo",
    )

    assert result.request_id == 11
    assert result.updated_existing is True
    assert request.id == 11
    manager.update_request.assert_called_once_with(request)
    manager.save_request.assert_not_called()


def test_request_persistence_facade_save_request_from_dialog_saves_new_request() -> None:
    manager = Mock()
    manager.save_request.return_value = 33
    facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)
    request = Request(method="GET", url="https://example.com")

    result = facade.save_request_from_dialog(
        request,
        existing_request_id=11,
        existing_collection_id=7,
        target_collection_id=99,
        name="Demo",
    )

    assert result.request_id == 33
    assert result.updated_existing is False
    assert request.id == 33
    manager.save_request.assert_called_once_with(request, collection_id=99, name="Demo")


def test_request_persistence_facade_delegates_auth_source_operations() -> None:
    manager = Mock()
    facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)
    request = Request(method="GET", url="https://example.com", collection_id=7)

    manager.resolve_effective_auth.return_value = ("auth", "collection")

    assert facade.resolve_effective_auth(request) == ("auth", "collection")
    facade.persist_auth_to_source(7, "collection", {"token": "abc"})
    facade.persist_auth_to_source(7, "folder:Nested", {"token": "def"})

    manager.resolve_effective_auth.assert_called_once_with(request)
    manager.set_collection_auth.assert_called_once_with(7, {"token": "abc"})
    manager.set_folder_auth.assert_called_once_with(7, "Nested", {"token": "def"})


def test_request_persistence_facade_persists_oauth2_tokens_only_when_valid() -> None:
    manager = Mock()
    facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)

    own_auth = OAuth2Auth(token_url="https://idp/token", client_id="cid")
    own_auth.access_token = "own-token"
    inherited_auth = OAuth2Auth(token_url="https://idp/token", client_id="cid")
    inherited_auth.access_token = "inh-token"

    saved_request = Request(method="GET", url="https://example.com", id=5, collection_id=7)

    assert facade.persist_request_oauth2_token(saved_request, own_auth) is True
    assert (
        facade.persist_inherited_oauth2_token(saved_request, "collection", inherited_auth) is True
    )
    assert (
        facade.persist_request_oauth2_token(
            Request(method="GET", url="https://example.com"), own_auth
        )
        is False
    )
    assert facade.persist_inherited_oauth2_token(saved_request, None, inherited_auth) is False

    manager.update_request_auth.assert_called_once_with(5, own_auth)
    manager.set_collection_auth.assert_called_once_with(7, inherited_auth)
