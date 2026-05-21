"""Extended coverage tests for equinox.application.collections.facade."""

from __future__ import annotations

from unittest.mock import Mock

from equinox.application.collections import CollectionFacade


def _make_facade(fetchone_values=None, fetchall_value=None):
    """Helper that builds a CollectionFacade with a mocked manager."""
    manager = Mock()
    manager.db = Mock()
    if fetchone_values is not None:
        manager.db.fetchone.side_effect = fetchone_values
    if fetchall_value is not None:
        manager.db.fetchall.return_value = fetchall_value
    return CollectionFacade(db=Mock(), collection_manager=manager), manager


class TestCollectionFacadeReadHelpers:
    def test_get_collection_delegates(self) -> None:
        facade, manager = _make_facade()
        manager.get_collection.return_value = {"id": 1, "name": "Col"}
        assert facade.get_collection(1) == {"id": 1, "name": "Col"}
        manager.get_collection.assert_called_once_with(1)

    def test_get_request_delegates(self) -> None:
        facade, manager = _make_facade()
        manager.get_request.return_value = None
        assert facade.get_request(5) is None
        manager.get_request.assert_called_once_with(5)

    def test_get_request_location_returns_none_when_no_row(self) -> None:
        facade, manager = _make_facade(fetchone_values=[None])
        result = facade.get_request_location(999)
        assert result is None

    def test_get_request_location_returns_tuple(self) -> None:
        facade, manager = _make_facade(fetchone_values=[{"collection_id": 3, "folder": "A"}])
        result = facade.get_request_location(7)
        assert result == (3, "A")

    def test_get_request_location_null_folder_becomes_none(self) -> None:
        facade, manager = _make_facade(fetchone_values=[{"collection_id": 3, "folder": None}])
        result = facade.get_request_location(7)
        assert result == (3, None)

    def test_list_group_request_ids(self) -> None:
        facade, manager = _make_facade(fetchall_value=[{"id": 10}, {"id": 20}])
        result = facade.list_group_request_ids(1, "MyFolder")
        assert result == [10, 20]


class TestCollectionFacadeLifecycle:
    def test_create_collection_delegates(self) -> None:
        facade, manager = _make_facade()
        manager.create_collection.return_value = 42
        assert facade.create_collection("New") == 42
        manager.create_collection.assert_called_once_with("New")

    def test_save_request_delegates(self) -> None:
        from equinox.core.request import Request

        facade, manager = _make_facade()
        manager.save_request.return_value = 55
        req = Request(method="GET", url="https://example.com")
        result = facade.save_request(req, collection_id=1, name="Demo")
        assert result == 55
        manager.save_request.assert_called_once_with(req, collection_id=1, name="Demo")


class TestCollectionFacadeFolderOperations:
    def test_create_folder_delegates(self) -> None:
        facade, manager = _make_facade()
        facade.create_folder(1, "Auth")
        manager.create_folder.assert_called_once_with(1, "Auth")

    def test_rename_folder_delegates(self) -> None:
        facade, manager = _make_facade()
        facade.rename_folder(1, "Old", "New")
        manager.rename_folder.assert_called_once_with(1, "Old", "New")

    def test_delete_folder_delegates(self) -> None:
        facade, manager = _make_facade()
        facade.delete_folder(1, "Auth", move_to_root=True)
        manager.delete_folder.assert_called_once_with(1, "Auth", move_to_root=True)

    def test_move_request_to_folder_delegates(self) -> None:
        facade, manager = _make_facade()
        facade.move_request_to_folder(7, "NewFolder")
        manager.move_request_to_folder.assert_called_once_with(7, "NewFolder")


class TestCollectionFacadeReorder:
    def test_reorder_before_target_aborts_when_dragged_not_found(self) -> None:
        """When get_request_location returns None for dragged, nothing happens."""
        facade, manager = _make_facade(
            fetchone_values=[
                {"collection_id": 10, "folder": "A"},  # target
                None,  # dragged → not found
            ]
        )
        facade.reorder_request_before_target(dragged_id=99, target_id=10)
        manager.reorder_requests.assert_not_called()

    def test_reorder_before_target_aborts_when_target_not_found(self) -> None:
        # Both get_request_location calls happen before the guard check;
        # supply two fetchone results (target=None, dragged also returns something
        # so no StopIteration, but the None target causes early return).
        facade, manager = _make_facade(fetchone_values=[None, None])
        facade.reorder_request_before_target(dragged_id=99, target_id=10)
        manager.reorder_requests.assert_not_called()

    def test_reorder_before_target_same_collection_different_folder(self) -> None:
        manager = Mock()
        manager.db = Mock()
        manager.db.fetchone.side_effect = [
            {"collection_id": 10, "folder": "FolderA"},  # target
            {"collection_id": 10, "folder": "FolderB"},  # dragged
        ]
        manager.db.fetchall.return_value = [{"id": 10}, {"id": 20}]
        facade = CollectionFacade(db=Mock(), collection_manager=manager)

        facade.reorder_request_before_target(dragged_id=99, target_id=10)

        manager.move_request_to_folder.assert_called_once_with(99, "FolderA")
        manager.move_request_to_collection.assert_not_called()

    def test_reorder_before_target_inserts_at_end_when_target_missing(self) -> None:
        """When target_id is not in the group list, dragged goes to the end."""
        manager = Mock()
        manager.db = Mock()
        manager.db.fetchone.side_effect = [
            {"collection_id": 10, "folder": None},  # target
            {"collection_id": 10, "folder": None},  # dragged
        ]
        # target_id=999 is NOT in the returned rows
        manager.db.fetchall.return_value = [{"id": 100}, {"id": 200}]
        facade = CollectionFacade(db=Mock(), collection_manager=manager)

        facade.reorder_request_before_target(dragged_id=50, target_id=999)

        # dragged_id=50 already excluded (not in list), insert_at=len=2 → appended
        manager.reorder_requests.assert_called_once_with([100, 200, 50])


class TestCollectionFacadeAuth:
    def test_get_collection_auth_delegates(self) -> None:
        facade, manager = _make_facade()
        manager.get_collection_auth.return_value = {"type": "bearer"}
        result = facade.get_collection_auth(1)
        assert result == {"type": "bearer"}
        manager.get_collection_auth.assert_called_once_with(1)

    def test_set_collection_auth_delegates(self) -> None:
        facade, manager = _make_facade()
        facade.set_collection_auth(1, {"type": "bearer"})
        manager.set_collection_auth.assert_called_once_with(1, {"type": "bearer"})

    def test_get_folder_auth_delegates(self) -> None:
        facade, manager = _make_facade()
        manager.get_folder_auth.return_value = None
        result = facade.get_folder_auth(1, "AuthFolder")
        assert result is None
        manager.get_folder_auth.assert_called_once_with(1, "AuthFolder")

    def test_set_folder_auth_delegates(self) -> None:
        facade, manager = _make_facade()
        facade.set_folder_auth(1, "AuthFolder", {"type": "basic"})
        manager.set_folder_auth.assert_called_once_with(1, "AuthFolder", {"type": "basic"})


class TestCollectionFacadeSorting:
    def test_sort_alphabetically_delegates(self) -> None:
        facade, manager = _make_facade()
        facade.sort_requests_alphabetically(1, None)
        manager.sort_requests_alphabetically.assert_called_once_with(1, None)

    def test_sort_by_method_delegates(self) -> None:
        facade, manager = _make_facade()
        facade.sort_requests_by_method(1, "Auth")
        manager.sort_requests_by_method.assert_called_once_with(1, "Auth")

