"""Extended tests for EnvironmentManager — covers validation paths, update,
delete, get, list, activation, and interpolate_variables."""

import pytest

from equinox.core.exceptions import SecurityError, StorageError, ValidationError
from equinox.storage.database import Database
from equinox.storage.environments import EnvironmentManager


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def mgr(db):
    return EnvironmentManager(db)


def _create(mgr, name="Test Env", variables=None, description=""):
    return mgr.create_environment(
        name=name,
        variables=variables if variables is not None else {"BASE_URL": "https://api.example.com"},
        description=description,
    )


# ── create_environment validation ─────────────────────────────────────────────


class TestCreateEnvironmentValidation:
    def test_empty_name_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("", {})

    def test_whitespace_name_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("   ", {})

    def test_name_too_long_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("x" * 201, {})

    def test_non_string_description_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("Env", {}, description=123)

    def test_description_too_long_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("Env", {}, description="d" * 1001)

    def test_variables_must_be_dict(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("Env", variables="not-a-dict")

    def test_too_many_variables_raises(self, mgr):
        big_vars = {f"KEY_{i}": "val" for i in range(101)}
        with pytest.raises(SecurityError):
            mgr.create_environment("Env", variables=big_vars)

    def test_variable_key_not_string_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("Env", variables={123: "value"})

    def test_variable_key_empty_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("Env", variables={"": "value"})

    def test_variable_key_too_long_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("Env", variables={"a" * 101: "value"})

    def test_variable_key_invalid_chars_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("Env", variables={"bad key!": "value"})

    def test_variable_value_too_long_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("Env", variables={"KEY": "v" * 10001})

    def test_successful_creation(self, mgr):
        eid = _create(mgr, "Production")
        assert eid >= 1

    def test_successful_creation_with_description(self, mgr):
        eid = mgr.create_environment(
            "Env With Desc", {"API_URL": "http://x.com"}, description="my desc"
        )
        env = mgr.get_environment(eid)
        assert env["description"] == "my desc"

    def test_duplicate_name_raises(self, mgr):
        _create(mgr, "UniqueEnv")
        with pytest.raises((StorageError, ValidationError)):
            _create(mgr, "UniqueEnv")


# ── get_environment ───────────────────────────────────────────────────────────


class TestGetEnvironment:
    def test_get_existing(self, mgr):
        eid = _create(mgr, "Dev")
        env = mgr.get_environment(eid)
        assert env is not None
        assert env["name"] == "Dev"
        assert isinstance(env["variables"], dict)

    def test_get_nonexistent_returns_none(self, mgr):
        assert mgr.get_environment(9999) is None

    def test_get_invalid_id_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.get_environment(0)
        with pytest.raises(ValidationError):
            mgr.get_environment(-1)


# ── list_environments ─────────────────────────────────────────────────────────


class TestListEnvironments:
    def test_list_empty(self, mgr):
        assert mgr.list_environments() == []

    def test_list_multiple(self, mgr):
        _create(mgr, "Dev")
        _create(mgr, "Prod")
        envs = mgr.list_environments()
        assert len(envs) == 2

    def test_list_returns_variable_dicts(self, mgr):
        _create(mgr, "Alpha", variables={"X": "1"})
        envs = mgr.list_environments()
        assert isinstance(envs[0]["variables"], dict)
        assert envs[0]["variables"]["X"] == "1"


# ── update_environment ────────────────────────────────────────────────────────


class TestUpdateEnvironment:
    def test_update_variables(self, mgr):
        eid = _create(mgr, "Updatable")
        mgr.update_environment(eid, variables={"NEW_KEY": "new_value"})
        env = mgr.get_environment(eid)
        assert env["variables"]["NEW_KEY"] == "new_value"

    def test_update_name(self, mgr):
        eid = _create(mgr, "Old Name")
        mgr.update_environment(eid, name="New Name")
        env = mgr.get_environment(eid)
        assert env["name"] == "New Name"

    def test_update_description(self, mgr):
        eid = _create(mgr, "Desc Env")
        mgr.update_environment(eid, description="New description")
        env = mgr.get_environment(eid)
        assert env["description"] == "New description"

    def test_update_nonexistent_raises(self, mgr):
        with pytest.raises((StorageError, ValidationError)):
            mgr.update_environment(9999, name="New")

    def test_update_invalid_id_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.update_environment(0, name="x")

    def test_update_invalid_variable_key_raises(self, mgr):
        eid = _create(mgr, "Valid Env")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, variables={"bad key!": "value"})

    def test_update_no_changes_is_no_op(self, mgr):
        eid = _create(mgr, "No Change Env")
        mgr.update_environment(eid)  # no keyword args — should not raise

    def test_update_name_too_long(self, mgr):
        eid = _create(mgr, "LongNameEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, name="x" * 201)

    def test_update_name_whitespace_raises(self, mgr):
        eid = _create(mgr, "WSNameEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, name="   ")

    def test_update_name_non_string_raises(self, mgr):
        eid = _create(mgr, "TypeEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, name=123)

    def test_update_description_non_string_raises(self, mgr):
        eid = _create(mgr, "DescTypeEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, description=999)

    def test_update_description_too_long_raises(self, mgr):
        eid = _create(mgr, "DescLenEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, description="d" * 1001)

    def test_update_variables_not_dict_raises(self, mgr):
        eid = _create(mgr, "VarTypeEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, variables="bad")

    def test_update_variables_too_many_raises(self, mgr):
        eid = _create(mgr, "VarCountEnv")
        big = {f"K_{i}": "v" for i in range(101)}
        with pytest.raises(SecurityError):
            mgr.update_environment(eid, variables=big)

    def test_update_variable_key_not_string_raises(self, mgr):
        eid = _create(mgr, "VarKeyEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, variables={42: "val"})

    def test_update_variable_key_empty_raises(self, mgr):
        eid = _create(mgr, "VarEmptyKeyEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, variables={"": "val"})

    def test_update_variable_key_too_long_raises(self, mgr):
        eid = _create(mgr, "VarKeyLenEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, variables={"k" * 101: "val"})

    def test_update_variable_value_not_string_raises(self, mgr):
        eid = _create(mgr, "VarValTypeEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, variables={"KEY": 42})

    def test_update_variable_value_too_long_raises(self, mgr):
        eid = _create(mgr, "VarValLenEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, variables={"KEY": "v" * 10001})

    def test_update_secret_keys(self, mgr):
        eid = _create(mgr, "SecKeysEnv")
        mgr.update_environment(eid, secret_keys=["API_KEY", "TOKEN"])
        env = mgr.get_environment(eid)
        assert env["secret_keys"] == ["API_KEY", "TOKEN"]

    def test_update_secret_keys_not_list_raises(self, mgr):
        eid = _create(mgr, "SecKeysTypeEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, secret_keys="bad")

    def test_update_secret_keys_too_many_raises(self, mgr):
        eid = _create(mgr, "SecKeysCountEnv")
        with pytest.raises(SecurityError):
            mgr.update_environment(eid, secret_keys=["k"] * 101)

    def test_update_secret_keys_non_string_entry_raises(self, mgr):
        eid = _create(mgr, "SecKeysEntryEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, secret_keys=[123])

    def test_update_secret_keys_entry_too_long_raises(self, mgr):
        eid = _create(mgr, "SecKeysLenEnv")
        with pytest.raises(ValidationError):
            mgr.update_environment(eid, secret_keys=["k" * 101])

    def test_update_duplicate_name_raises(self, mgr):
        _create(mgr, "EnvA")
        eid_b = _create(mgr, "EnvB")
        with pytest.raises(StorageError, match="already exists"):
            mgr.update_environment(eid_b, name="EnvA")


# ── delete_environment ────────────────────────────────────────────────────────


class TestDeleteEnvironment:
    def test_delete_existing(self, mgr):
        eid = _create(mgr, "To Delete")
        mgr.delete_environment(eid)
        assert mgr.get_environment(eid) is None

    def test_delete_nonexistent_raises(self, mgr):
        with pytest.raises((StorageError, ValidationError)):
            mgr.delete_environment(9999)

    def test_delete_invalid_id_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.delete_environment(0)


# ── set_active_environment / get_active_environment ───────────────────────────


class TestActivation:
    def test_no_active_by_default(self, mgr):
        _create(mgr, "Inactive")
        assert mgr.get_active_environment() is None

    def test_activate_environment(self, mgr):
        eid = _create(mgr, "Activatable")
        mgr.set_active_environment(eid)
        active = mgr.get_active_environment()
        assert active is not None
        assert active["id"] == eid

    def test_activate_switches_from_previous(self, mgr):
        e1 = _create(mgr, "First")
        e2 = _create(mgr, "Second")
        mgr.set_active_environment(e1)
        mgr.set_active_environment(e2)
        active = mgr.get_active_environment()
        assert active["id"] == e2
        # first should now be inactive
        assert mgr.get_environment(e1)["is_active"] == 0

    def test_activate_nonexistent_raises(self, mgr):
        with pytest.raises((StorageError, ValidationError)):
            mgr.set_active_environment(9999)

    def test_activate_invalid_id_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.set_active_environment(0)


# ── interpolate_variables ─────────────────────────────────────────────────────


class TestInterpolateVariables:
    def test_no_active_env_returns_unchanged(self, mgr):
        result = mgr.interpolate_variables("Hello {{NAME}}")
        assert result == "Hello {{NAME}}"

    def test_replaces_variables(self, mgr):
        eid = _create(mgr, "Interp Env", variables={"HOST": "example.com"})
        mgr.set_active_environment(eid)
        result = mgr.interpolate_variables("https://{{HOST}}/api")
        assert result == "https://example.com/api"

    def test_invalid_text_type_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.interpolate_variables(12345)

    def test_text_too_large_raises(self, mgr):
        big_text = "x" * 1_000_001
        with pytest.raises(SecurityError):
            mgr.interpolate_variables(big_text)

    def test_text_with_no_placeholders(self, mgr):
        eid = _create(mgr, "No Placeholders Env", variables={"X": "y"})
        mgr.set_active_environment(eid)
        text = "just plain text"
        assert mgr.interpolate_variables(text) == text

    def test_empty_variables_returns_unchanged(self, mgr):
        eid = _create(mgr, "Empty Vars", variables={})
        mgr.set_active_environment(eid)
        result = mgr.interpolate_variables("{{MISSING}}")
        assert result == "{{MISSING}}"

    def test_multiple_variables(self, mgr):
        eid = _create(mgr, "Multi", variables={"A": "1", "B": "2"})
        mgr.set_active_environment(eid)
        assert mgr.interpolate_variables("{{A}} and {{B}}") == "1 and 2"

    def test_variable_value_not_string_raises(self, mgr):
        with pytest.raises(ValidationError):
            mgr.create_environment("BadVal", variables={"KEY": 42})
