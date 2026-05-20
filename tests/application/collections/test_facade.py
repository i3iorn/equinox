from __future__ import annotations

from unittest.mock import Mock

from equinox.application.collections import CollectionFacade


def test_collection_facade_delegates_basic_operations() -> None:
    manager = Mock()
    facade = CollectionFacade(db=Mock(), collection_manager=manager)

    facade.rename_collection(1, "Renamed")
    facade.rename_request(2, "Req")
    facade.duplicate_request(2)
    facade.delete_collection(1)
    facade.delete_request(2)

    manager.rename_collection.assert_called_once_with(1, "Renamed")
    manager.rename_request.assert_called_once_with(2, "Req")
    manager.duplicate_request.assert_called_once_with(2)
    manager.delete_collection.assert_called_once_with(1)
    manager.delete_request.assert_called_once_with(2)


def test_collection_facade_reorder_request_before_target_same_group() -> None:
    manager = Mock()
    manager.db = Mock()
    manager.db.fetchone.side_effect = [
        {"collection_id": 10, "folder": "A"},
        {"collection_id": 10, "folder": "A"},
    ]
    manager.db.fetchall.return_value = [{"id": 100}, {"id": 200}, {"id": 300}]
    facade = CollectionFacade(db=Mock(), collection_manager=manager)

    facade.reorder_request_before_target(dragged_id=300, target_id=100)

    manager.move_request_to_collection.assert_not_called()
    manager.move_request_to_folder.assert_not_called()
    manager.reorder_requests.assert_called_once_with([300, 100, 200])


def test_collection_facade_reorder_request_before_target_cross_collection() -> None:
    manager = Mock()
    manager.db = Mock()
    manager.db.fetchone.side_effect = [
        {"collection_id": 10, "folder": "A"},
        {"collection_id": 20, "folder": "B"},
    ]
    manager.db.fetchall.return_value = [{"id": 10}, {"id": 11}]
    facade = CollectionFacade(db=Mock(), collection_manager=manager)

    facade.reorder_request_before_target(dragged_id=99, target_id=10)

    manager.move_request_to_collection.assert_called_once_with(99, 10, "A")
    manager.reorder_requests.assert_called_once_with([99, 10, 11])

