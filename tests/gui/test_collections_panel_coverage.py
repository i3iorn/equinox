import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QTreeWidgetItem, QInputDialog, QMessageBox
from equinox.gui.collections_panel_pkg.panel import CollectionsPanel
from equinox.storage import Database

@pytest.fixture
def app():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app

@pytest.fixture
def db():
    return MagicMock(spec=Database)

@pytest.fixture
def panel(app, db):
    with patch("equinox.gui.collections_panel_pkg.panel.CollectionsPanel._setup_auto_refresh"):
        p = CollectionsPanel(db)
        return p

def test_refresh_empty(panel):
    panel.db.get_collections.return_value = []
    panel.refresh()
    assert panel.tree.topLevelItemCount() == 0

def test_refresh_with_data(panel):
    # Mock collections and requests
    panel.db.get_collections.return_value = [
        {"id": 1, "name": "Col 1"}
    ]
    panel.db.get_requests_for_collection.return_value = [
        {"id": 101, "name": "Req 1", "method": "GET", "url": "http://test.com", "folder_path": None}
    ]
    panel.db.get_folders.return_value = []
    
    panel.refresh()
    assert panel.tree.topLevelItemCount() == 1
    col_item = panel.tree.topLevelItem(0)
    assert col_item.text(0) == "Col 1"
    assert col_item.childCount() == 1
    assert col_item.child(0).text(0) == "Req 1"

def test_create_collection(panel):
    with patch.object(QInputDialog, 'getText', return_value=("New Col", True)):
        panel.create_collection()
        panel.db.create_collection.assert_called_once_with("New Col")

def test_rename_collection(panel):
    item = MagicMock(spec=QTreeWidgetItem)
    item.data.return_value = 1 # col_id
    item.text.return_value = "Old Name"
    
    with patch.object(QInputDialog, 'getText', return_value=("New Name", True)):
        panel._rename_collection(1, item)
        panel.db.rename_collection.assert_called_once_with(1, "New Name")

def test_delete_collection(panel):
    with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
        panel._delete_collection(1)
        panel.db.delete_collection.assert_called_once_with(1)

def test_rename_request(panel):
    item = MagicMock(spec=QTreeWidgetItem)
    item.data.return_value = 101 # request_id
    item.text.return_value = "Old Req"
    
    with patch.object(QInputDialog, 'getText', return_value=("New Req", True)):
        panel._rename_request(101, item)
        panel.db.rename_request.assert_called_once_with(101, "New Req")

def test_delete_request(panel):
    with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
        panel._delete_request(101)
        panel.db.delete_request.assert_called_once_with(101)

def test_duplicate_request(panel):
    panel._duplicate_request(101)
    panel.db.duplicate_request.assert_called_once_with(101)

def test_filter_collections(panel):
    # Setup some items
    col_item = QTreeWidgetItem(panel.tree, ["My Collection"])
    req_item = QTreeWidgetItem(col_item, ["Get Users"])
    
    panel._apply_filter("Users")
    assert not col_item.isHidden()
    assert not req_item.isHidden()
    
    panel._apply_filter("Nomatch")
    assert col_item.isHidden()
