"""Tests for global variable storage manager."""

from equinox.storage import Database
from equinox.storage.global_variables import GlobalVariablesManager


def test_set_and_get_global_variable(tmp_path) -> None:
    db = Database(str(tmp_path / "globals.db"))
    mgr = GlobalVariablesManager(db)

    mgr.set_variable("BASE_URL", "https://api.example.com", "default host")
    row = mgr.get_variable("BASE_URL")

    assert row is not None
    assert row["value"] == "https://api.example.com"
    assert row["description"] == "default host"


def test_set_variable_updates_existing_key(tmp_path) -> None:
    db = Database(str(tmp_path / "globals_update.db"))
    mgr = GlobalVariablesManager(db)

    mgr.set_variable("BASE_URL", "https://one.example")
    mgr.set_variable("BASE_URL", "https://two.example")

    assert mgr.get_variables_dict()["BASE_URL"] == "https://two.example"
    assert len(mgr.list_variables()) == 1


def test_remove_global_variable(tmp_path) -> None:
    db = Database(str(tmp_path / "globals_remove.db"))
    mgr = GlobalVariablesManager(db)

    mgr.set_variable("TOKEN", "abc")
    mgr.remove_variable("TOKEN")

    assert mgr.get_variable("TOKEN") is None

