"""Extended coverage tests for equinox.application.requests.persistence."""

from __future__ import annotations

from unittest.mock import Mock

from equinox.application.requests import RequestPersistenceFacade
from equinox.auth import OAuth2Auth
from equinox.core.request import Request


class TestRequestPersistenceFacadeProperty:
    def test_collection_manager_property_returns_manager(self) -> None:
        manager = Mock()
        facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)
        assert facade.collection_manager is manager


class TestListSaveCollectionsExisting:
    def test_list_save_collections_with_existing_no_default_created(self) -> None:
        manager = Mock()
        manager.list_collections.return_value = [
            {"id": 1, "name": "Existing Col"},
        ]
        facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)

        result = facade.list_save_collections()

        assert result == [{"id": 1, "name": "Existing Col"}]
        manager.create_collection.assert_not_called()


class TestPersistAuthToSource:
    def test_unknown_source_logs_warning_and_does_nothing(self) -> None:
        manager = Mock()
        facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)

        facade.persist_auth_to_source(1, "unknown-source", {"type": "bearer"})

        manager.set_collection_auth.assert_not_called()
        manager.set_folder_auth.assert_not_called()

    def test_folder_prefix_strips_correctly(self) -> None:
        manager = Mock()
        facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)

        facade.persist_auth_to_source(3, "folder:Deep/Nested", {"type": "basic"})

        manager.set_folder_auth.assert_called_once_with(3, "Deep/Nested", {"type": "basic"})


class TestPersistInheritedOAuth2Token:
    def _make_oauth2(self, token: str = "tok") -> OAuth2Auth:
        auth = OAuth2Auth(token_url="https://idp/token", client_id="cid")
        auth.access_token = token
        return auth

    def test_returns_false_when_request_is_none(self) -> None:
        facade = RequestPersistenceFacade(db=Mock(), collection_manager=Mock())
        assert facade.persist_inherited_oauth2_token(None, "collection", self._make_oauth2()) is False

    def test_returns_false_when_source_is_none(self) -> None:
        facade = RequestPersistenceFacade(db=Mock(), collection_manager=Mock())
        request = Request(method="GET", url="https://example.com", collection_id=1)
        assert facade.persist_inherited_oauth2_token(request, None, self._make_oauth2()) is False

    def test_returns_false_when_auth_has_no_access_token(self) -> None:
        facade = RequestPersistenceFacade(db=Mock(), collection_manager=Mock())
        request = Request(method="GET", url="https://example.com", collection_id=1)
        auth = OAuth2Auth(token_url="https://idp/token", client_id="cid")
        auth.access_token = ""
        assert facade.persist_inherited_oauth2_token(request, "collection", auth) is False

    def test_returns_false_when_collection_id_is_not_int(self) -> None:
        facade = RequestPersistenceFacade(db=Mock(), collection_manager=Mock())
        request = Request(method="GET", url="https://example.com")
        # No collection_id → getattr returns None → not isinstance(int) → False
        assert facade.persist_inherited_oauth2_token(request, "collection", self._make_oauth2()) is False

    def test_returns_false_when_auth_not_oauth2(self) -> None:
        from equinox.auth import BearerAuth

        facade = RequestPersistenceFacade(db=Mock(), collection_manager=Mock())
        request = Request(method="GET", url="https://example.com", collection_id=1)
        assert facade.persist_inherited_oauth2_token(request, "collection", BearerAuth(token="t")) is False

    def test_persist_request_oauth2_token_returns_false_for_non_int_id(self) -> None:
        manager = Mock()
        facade = RequestPersistenceFacade(db=Mock(), collection_manager=manager)
        auth = self._make_oauth2()

        # Request with a string id (edge case)
        request = Request(method="GET", url="https://example.com")
        object.__setattr__(request, "id", "not-an-int")  # force a non-int id

        result = facade.persist_request_oauth2_token(request, auth)
        assert result is False

