"""Tests for variables and variable groups"""

import pytest
import tempfile
from pathlib import Path

from equinox.storage import Database, CollectionManager, VariableGroupManager
from equinox.core.exceptions import ValidationError, StorageError, SecurityError


class TestVariableGroups:
    """Test variable group management"""

    @pytest.fixture
    def db(self):
        """Create temporary database"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        db = Database(db_path)
        yield db

        # Cleanup
        Path(db_path).unlink(missing_ok=True)

    @pytest.fixture
    def var_mgr(self, db):
        """Create variable group manager"""
        return VariableGroupManager(db)

    def test_create_group(self, var_mgr):
        """Test creating a variable group"""
        group_id = var_mgr.create_group("API Config", "API configuration variables")
        assert group_id > 0

        group = var_mgr.get_group(group_id)
        assert group is not None
        assert group["name"] == "API Config"
        assert group["description"] == "API configuration variables"

    def test_create_group_validation(self, var_mgr):
        """Test variable group creation validation"""
        # Empty name
        with pytest.raises(ValidationError, match="non-empty string"):
            var_mgr.create_group("", "Description")

        # Whitespace only
        with pytest.raises(ValidationError, match="empty or whitespace"):
            var_mgr.create_group("   ", "Description")

        # Name too long
        with pytest.raises(ValidationError, match="too long"):
            var_mgr.create_group("A" * 300, "Description")

        # Description too long
        with pytest.raises(ValidationError, match="too long"):
            var_mgr.create_group("Valid Name", "D" * 2000)

    def test_create_duplicate_group(self, var_mgr):
        """Test creating duplicate group fails"""
        var_mgr.create_group("Test Group", "First")

        with pytest.raises(StorageError, match="already exists"):
            var_mgr.create_group("Test Group", "Second")

    def test_list_groups(self, var_mgr):
        """Test listing variable groups"""
        # Initially empty
        groups = var_mgr.list_groups()
        assert len(groups) == 0

        # Add groups
        var_mgr.create_group("Group A", "First group")
        var_mgr.create_group("Group B", "Second group")
        var_mgr.create_group("Group C", "Third group")

        groups = var_mgr.list_groups()
        assert len(groups) == 3
        # Should be sorted by name
        assert groups[0]["name"] == "Group A"
        assert groups[1]["name"] == "Group B"
        assert groups[2]["name"] == "Group C"

    def test_update_group(self, var_mgr):
        """Test updating a variable group"""
        group_id = var_mgr.create_group("Old Name", "Old description")

        var_mgr.update_group(group_id, name="New Name", description="New description")

        group = var_mgr.get_group(group_id)
        assert group["name"] == "New Name"
        assert group["description"] == "New description"

    def test_update_group_validation(self, var_mgr):
        """Test update validation"""
        group_id = var_mgr.create_group("Test", "Description")

        # Invalid group ID
        with pytest.raises(ValidationError, match="positive integer"):
            var_mgr.update_group(-1, name="New Name")

        # Non-existent group
        with pytest.raises(StorageError, match="does not exist"):
            var_mgr.update_group(99999, name="New Name")

        # Name too long
        with pytest.raises(ValidationError, match="too long"):
            var_mgr.update_group(group_id, name="A" * 300)

    def test_delete_group(self, var_mgr):
        """Test deleting a variable group"""
        group_id = var_mgr.create_group("To Delete", "Will be deleted")

        # Add some variables
        var_mgr.add_variable(group_id, "VAR1", "value1")
        var_mgr.add_variable(group_id, "VAR2", "value2")

        var_mgr.delete_group(group_id)

        # Group should be gone
        group = var_mgr.get_group(group_id)
        assert group is None

        # Variables should be gone too (cascade delete)
        vars = var_mgr.list_group_variables(group_id)
        assert len(vars) == 0

    def test_delete_group_validation(self, var_mgr):
        """Test delete validation"""
        # Invalid ID
        with pytest.raises(ValidationError, match="positive integer"):
            var_mgr.delete_group(0)

        # Non-existent group
        with pytest.raises(StorageError, match="does not exist"):
            var_mgr.delete_group(99999)

    def test_add_variable(self, var_mgr):
        """Test adding variables to a group"""
        group_id = var_mgr.create_group("Test Group", "For testing")

        var_id = var_mgr.add_variable(group_id, "API_URL", "https://api.example.com", "Base URL")
        assert var_id > 0

        vars = var_mgr.list_group_variables(group_id)
        assert len(vars) == 1
        assert vars[0]["key"] == "API_URL"
        assert vars[0]["value"] == "https://api.example.com"
        assert vars[0]["description"] == "Base URL"

    def test_add_variable_updates_existing(self, var_mgr):
        """Test adding variable with same key updates value"""
        group_id = var_mgr.create_group("Test Group", "For testing")

        var_mgr.add_variable(group_id, "API_URL", "https://old.example.com")
        var_mgr.add_variable(group_id, "API_URL", "https://new.example.com")

        vars = var_mgr.list_group_variables(group_id)
        assert len(vars) == 1
        assert vars[0]["value"] == "https://new.example.com"

    def test_add_variable_validation(self, var_mgr):
        """Test variable addition validation"""
        group_id = var_mgr.create_group("Test", "Test")

        # Empty key
        with pytest.raises(ValidationError, match="non-empty string"):
            var_mgr.add_variable(group_id, "", "value")

        # Key too long
        with pytest.raises(ValidationError, match="too long"):
            var_mgr.add_variable(group_id, "K" * 200, "value")

        # Value too long
        with pytest.raises(ValidationError, match="too long"):
            var_mgr.add_variable(group_id, "KEY", "V" * 20000)

        # Non-existent group
        with pytest.raises(StorageError, match="does not exist"):
            var_mgr.add_variable(99999, "KEY", "value")

    def test_remove_variable(self, var_mgr):
        """Test removing a variable from a group"""
        group_id = var_mgr.create_group("Test Group", "For testing")

        var_mgr.add_variable(group_id, "VAR1", "value1")
        var_mgr.add_variable(group_id, "VAR2", "value2")

        var_mgr.remove_variable(group_id, "VAR1")

        vars = var_mgr.list_group_variables(group_id)
        assert len(vars) == 1
        assert vars[0]["key"] == "VAR2"

    def test_get_group_variables_dict(self, var_mgr):
        """Test getting variables as dictionary"""
        group_id = var_mgr.create_group("Test Group", "For testing")

        var_mgr.add_variable(group_id, "API_URL", "https://api.example.com")
        var_mgr.add_variable(group_id, "API_KEY", "secret123")
        var_mgr.add_variable(group_id, "TIMEOUT", "30")

        vars_dict = var_mgr.get_group_variables_dict(group_id)

        assert isinstance(vars_dict, dict)
        assert len(vars_dict) == 3
        assert vars_dict["API_URL"] == "https://api.example.com"
        assert vars_dict["API_KEY"] == "secret123"
        assert vars_dict["TIMEOUT"] == "30"


class TestCollectionVariables:
    """Test collection variable management"""

    @pytest.fixture
    def db(self):
        """Create temporary database"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        db = Database(db_path)
        yield db

        # Cleanup
        Path(db_path).unlink(missing_ok=True)

    @pytest.fixture
    def col_mgr(self, db):
        """Create collection manager"""
        return CollectionManager(db)

    @pytest.fixture
    def collection_id(self, col_mgr):
        """Create a test collection"""
        return col_mgr.create_collection("Test Collection", "For testing")

    def test_add_collection_variable(self, col_mgr, collection_id):
        """Test adding a variable to a collection"""
        var_id = col_mgr.add_variable(collection_id, "USER_ID", "12345", "Test user")
        assert var_id > 0

        vars = col_mgr.list_collection_variables(collection_id)
        assert len(vars) == 1
        assert vars[0]["key"] == "USER_ID"
        assert vars[0]["value"] == "12345"

    def test_add_collection_variable_validation(self, col_mgr, collection_id):
        """Test collection variable validation"""
        # Empty key
        with pytest.raises(ValidationError, match="non-empty string"):
            col_mgr.add_variable(collection_id, "", "value")

        # Invalid collection ID
        with pytest.raises(ValidationError, match="positive integer"):
            col_mgr.add_variable(-1, "KEY", "value")

        # Non-existent collection
        with pytest.raises(StorageError, match="does not exist"):
            col_mgr.add_variable(99999, "KEY", "value")

    def test_remove_collection_variable(self, col_mgr, collection_id):
        """Test removing a collection variable"""
        col_mgr.add_variable(collection_id, "VAR1", "value1")
        col_mgr.add_variable(collection_id, "VAR2", "value2")

        col_mgr.remove_variable(collection_id, "VAR1")

        vars = col_mgr.list_collection_variables(collection_id)
        assert len(vars) == 1
        assert vars[0]["key"] == "VAR2"

    def test_get_collection_variables_dict(self, col_mgr, collection_id):
        """Test getting collection variables as dictionary"""
        col_mgr.add_variable(collection_id, "API_URL", "https://api.example.com")
        col_mgr.add_variable(collection_id, "TOKEN", "abc123")

        vars_dict = col_mgr.get_collection_variables_dict(collection_id)

        assert isinstance(vars_dict, dict)
        assert len(vars_dict) == 2
        assert vars_dict["API_URL"] == "https://api.example.com"
        assert vars_dict["TOKEN"] == "abc123"

    def test_add_variable_group_to_collection(self, db, col_mgr, collection_id):
        """Test adding a variable group to a collection"""
        var_mgr = VariableGroupManager(db)

        # Create a group
        group_id = var_mgr.create_group("Test Group", "For testing")
        var_mgr.add_variable(group_id, "VAR1", "value1")

        # Add to collection
        assoc_id = col_mgr.add_variable_group(collection_id, group_id, priority=5)
        assert assoc_id > 0

        groups = col_mgr.list_collection_variable_groups(collection_id)
        assert len(groups) == 1
        assert groups[0]["id"] == group_id
        assert groups[0]["priority"] == 5

    def test_remove_variable_group_from_collection(self, db, col_mgr, collection_id):
        """Test removing a variable group from a collection"""
        var_mgr = VariableGroupManager(db)

        group_id = var_mgr.create_group("Test Group", "For testing")
        col_mgr.add_variable_group(collection_id, group_id)

        col_mgr.remove_variable_group(collection_id, group_id)

        groups = col_mgr.list_collection_variable_groups(collection_id)
        assert len(groups) == 0

    def test_get_all_collection_variables_from_groups(self, db, col_mgr, collection_id):
        """Test getting merged variables from groups"""
        var_mgr = VariableGroupManager(db)

        # Create groups
        group1_id = var_mgr.create_group("Group 1", "First")
        var_mgr.add_variable(group1_id, "VAR1", "group1_value")
        var_mgr.add_variable(group1_id, "VAR2", "group1_var2")

        group2_id = var_mgr.create_group("Group 2", "Second")
        var_mgr.add_variable(group2_id, "VAR2", "group2_value")
        var_mgr.add_variable(group2_id, "VAR3", "group2_var3")

        # Add to collection with different priorities
        col_mgr.add_variable_group(collection_id, group1_id, priority=10)  # Lower priority
        col_mgr.add_variable_group(collection_id, group2_id, priority=5)   # Higher priority

        all_vars = col_mgr.get_all_collection_variables(collection_id)

        # VAR2 should come from group2 (higher priority)
        assert all_vars["VAR1"] == "group1_value"
        assert all_vars["VAR2"] == "group2_value"  # Higher priority wins
        assert all_vars["VAR3"] == "group2_var3"

    def test_collection_variables_override_groups(self, db, col_mgr, collection_id):
        """Test that collection variables override group variables"""
        var_mgr = VariableGroupManager(db)

        # Create group
        group_id = var_mgr.create_group("API Config", "Config")
        var_mgr.add_variable(group_id, "API_URL", "https://api.example.com")
        var_mgr.add_variable(group_id, "TIMEOUT", "30")

        # Add group to collection
        col_mgr.add_variable_group(collection_id, group_id)

        # Add collection-specific override
        col_mgr.add_variable(collection_id, "API_URL", "https://staging.api.example.com")

        all_vars = col_mgr.get_all_collection_variables(collection_id)

        # Collection variable should override group
        assert all_vars["API_URL"] == "https://staging.api.example.com"
        # Other group variable should still be there
        assert all_vars["TIMEOUT"] == "30"

    def test_variable_precedence_complex(self, db, col_mgr, collection_id):
        """Test complex variable precedence with multiple groups and overrides"""
        var_mgr = VariableGroupManager(db)

        # Create groups with different priorities
        common_id = var_mgr.create_group("Common", "Common settings")
        var_mgr.add_variable(common_id, "TIMEOUT", "10")
        var_mgr.add_variable(common_id, "RETRY", "1")
        var_mgr.add_variable(common_id, "API_URL", "https://default.example.com")

        env_id = var_mgr.create_group("Environment", "Environment settings")
        var_mgr.add_variable(env_id, "API_URL", "https://env.example.com")
        var_mgr.add_variable(env_id, "TIMEOUT", "20")

        # Add groups (lower priority number = higher priority)
        col_mgr.add_variable_group(collection_id, common_id, priority=100)  # Low priority
        col_mgr.add_variable_group(collection_id, env_id, priority=10)      # High priority

        # Add collection override
        col_mgr.add_variable(collection_id, "API_URL", "https://collection.example.com")

        all_vars = col_mgr.get_all_collection_variables(collection_id)

        # Collection variable wins
        assert all_vars["API_URL"] == "https://collection.example.com"
        # Environment (higher priority) wins over common
        assert all_vars["TIMEOUT"] == "20"
        # Only in common group
        assert all_vars["RETRY"] == "1"

    def test_multiple_collections_share_group(self, db, col_mgr):
        """Test that multiple collections can share the same variable group"""
        var_mgr = VariableGroupManager(db)

        # Create shared group
        shared_id = var_mgr.create_group("Shared Config", "Shared settings")
        var_mgr.add_variable(shared_id, "SHARED_VAR", "shared_value")

        # Create two collections
        col1_id = col_mgr.create_collection("Collection 1", "First")
        col2_id = col_mgr.create_collection("Collection 2", "Second")

        # Add same group to both
        col_mgr.add_variable_group(col1_id, shared_id)
        col_mgr.add_variable_group(col2_id, shared_id)

        # Both should have the variable
        vars1 = col_mgr.get_all_collection_variables(col1_id)
        vars2 = col_mgr.get_all_collection_variables(col2_id)

        assert vars1["SHARED_VAR"] == "shared_value"
        assert vars2["SHARED_VAR"] == "shared_value"

        # Override in one collection shouldn't affect the other
        col_mgr.add_variable(col1_id, "SHARED_VAR", "override_value")

        vars1_after = col_mgr.get_all_collection_variables(col1_id)
        vars2_after = col_mgr.get_all_collection_variables(col2_id)

        assert vars1_after["SHARED_VAR"] == "override_value"
        assert vars2_after["SHARED_VAR"] == "shared_value"  # Unchanged

    def test_cascade_delete_collection_removes_variables(self, col_mgr, collection_id):
        """Test that deleting a collection removes its variables"""
        col_mgr.add_variable(collection_id, "VAR1", "value1")
        col_mgr.add_variable(collection_id, "VAR2", "value2")

        col_mgr.delete_collection(collection_id)

        # Variables should be gone (cascade delete)
        vars = col_mgr.list_collection_variables(collection_id)
        assert len(vars) == 0

    def test_cascade_delete_group_removes_items(self, db):
        """Test that deleting a variable group removes its items"""
        var_mgr = VariableGroupManager(db)

        group_id = var_mgr.create_group("Test", "Test")
        var_mgr.add_variable(group_id, "VAR1", "value1")
        var_mgr.add_variable(group_id, "VAR2", "value2")

        var_mgr.delete_group(group_id)

        # Variables should be gone
        vars = var_mgr.list_group_variables(group_id)
        assert len(vars) == 0
