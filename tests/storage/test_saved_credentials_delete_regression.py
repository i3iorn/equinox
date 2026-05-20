import pytest

from equinox.core.exceptions import StorageError
from equinox.storage.database import Database
from equinox.storage.saved_credentials import SavedCredentialsManager


def test_delete_succeeds(tmp_path):
    db_path = tmp_path / "equinox_test.db"
    with Database(str(db_path)) as db:
        mgr = SavedCredentialsManager(db)

        cred_id = mgr.create("cred1", "bearer", {"token": "t"})
        assert mgr.get(cred_id) is not None

        mgr.delete(cred_id)
        assert mgr.get(cred_id) is None


def test_delete_missing_raises(tmp_path):
    db_path = tmp_path / "equinox_test2.db"
    with Database(str(db_path)) as db:
        mgr = SavedCredentialsManager(db)

        with pytest.raises(StorageError):
            mgr.delete(9999)
