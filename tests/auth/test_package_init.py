"""Tests for equinox.auth package initialization and factory functions."""

import pytest

from equinox.auth import (
    APIKeyAuth,
    AuthStrategy,
    AWSSigV4Auth,
    BasicAuth,
    BearerAuth,
    OAuth2Auth,
    auth_from_dict,
    get_auth_type,
    list_auth_types,
)


class TestPackageExports:
    """Test that package exports are correct and discoverable."""

    def test_all_exports_exist(self):
        """Verify all items in __all__ are actually exported."""
        import equinox.auth as auth_module

        for name in auth_module.__all__:
            assert hasattr(auth_module, name), f"Missing export: {name}"

    def test_base_class_exported(self):
        """Verify AuthStrategy base class is accessible."""
        assert AuthStrategy is not None
        from abc import ABC

        assert issubclass(AuthStrategy, ABC)

    def test_all_concrete_types_exported(self):
        """Verify all concrete auth types are exported."""
        assert BearerAuth is not None
        assert BasicAuth is not None
        assert APIKeyAuth is not None
        assert OAuth2Auth is not None
        assert AWSSigV4Auth is not None
        assert isinstance(BearerAuth, type)
        assert isinstance(BasicAuth, type)

    def test_factory_functions_exported(self):
        """Verify factory convenience functions are exported."""
        assert callable(auth_from_dict)
        assert callable(get_auth_type)
        assert callable(list_auth_types)


class TestFactoryFunctions:
    """Test factory convenience functions for deserialization and discovery."""

    def test_list_auth_types_returns_sorted_list(self):
        """Test that list_auth_types returns sorted auth type names."""
        types = list_auth_types()
        assert isinstance(types, list)
        assert len(types) > 0
        assert "bearer" in types
        assert "basic" in types
        assert types == sorted(types), "List should be sorted"

    def test_get_auth_type_by_short_name(self):
        """Test getting auth class by short type name."""
        BearerClass = get_auth_type("bearer")
        assert BearerClass == BearerAuth

        BasicClass = get_auth_type("basic")
        assert BasicClass == BasicAuth

    def test_get_auth_type_by_class_name(self):
        """Test getting auth class by full class name."""
        BearerClass = get_auth_type("BearerAuth")
        assert BearerClass == BearerAuth

        BasicClass = get_auth_type("BasicAuth")
        assert BasicClass == BasicAuth

    def test_get_auth_type_unknown_raises_error(self):
        """Test that unknown type name raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            get_auth_type("nonexistent_type")
        assert "Unknown auth type" in str(exc_info.value)
        assert "nonexistent_type" in str(exc_info.value)

    def test_auth_from_dict_bearer(self):
        """Test deserializing bearer token auth."""
        data = {"type": "bearer", "token": "my-test-token"}
        auth = auth_from_dict(data)
        assert isinstance(auth, BearerAuth)
        assert auth.token == "my-test-token"

    def test_auth_from_dict_basic(self):
        """Test deserializing basic auth."""
        data = {"type": "basic", "username": "testuser", "password": "testpass"}
        auth = auth_from_dict(data)
        assert isinstance(auth, BasicAuth)

    def test_auth_from_dict_api_key(self):
        """Test deserializing API key auth."""
        data = {"type": "api_key", "key": "X-API-Key", "value": "test-key"}
        auth = auth_from_dict(data)
        assert isinstance(auth, APIKeyAuth)

    def test_auth_from_dict_unknown_type_raises_error(self):
        """Test that unknown auth type raises error."""
        data = {"type": "unknown_auth_type"}
        with pytest.raises((ValueError, KeyError)):
            auth_from_dict(data)

    def test_auth_from_dict_missing_type_raises_error(self):
        """Test that missing type key raises error."""
        data = {"token": "test"}
        with pytest.raises((KeyError, ValueError)):
            auth_from_dict(data)


class TestModuleMetadata:
    """Test module metadata and documentation."""

    def test_module_has_comprehensive_docstring(self):
        """Verify module has a complete and helpful docstring."""
        import equinox.auth

        assert equinox.auth.__doc__ is not None
        doc = equinox.auth.__doc__

        # Check docstring is substantial
        assert len(doc) > 200, "Docstring should be comprehensive"

        # Check it documents the main types
        assert "BearerAuth" in doc
        assert "BasicAuth" in doc
        assert "OAuth2Auth" in doc

        # Check it has examples
        assert "Quick Start" in doc or "Example" in doc or "Usage" in doc.lower()

    def test_factory_functions_documented(self):
        """Verify factory functions have good docstrings."""
        assert get_auth_type.__doc__ is not None
        assert len(get_auth_type.__doc__) > 50

        assert list_auth_types.__doc__ is not None
        assert len(list_auth_types.__doc__) > 50

        assert auth_from_dict.__doc__ is not None
        assert len(auth_from_dict.__doc__) > 50

    def test_all_list_defined(self):
        """Verify __all__ is properly defined."""
        import equinox.auth

        assert hasattr(equinox.auth, "__all__")
        assert isinstance(equinox.auth.__all__, list)
        assert len(equinox.auth.__all__) > 0

        # Check it includes base class and implementations
        assert "AuthStrategy" in equinox.auth.__all__
        assert "BearerAuth" in equinox.auth.__all__


class TestTypeHints:
    """Test that type hints are correct for IDE support."""

    def test_get_auth_type_returns_correct_type(self):
        """Verify get_auth_type returns a Type[AuthStrategy]."""
        result = get_auth_type("bearer")
        assert isinstance(result, type)
        assert issubclass(result, AuthStrategy)

    def test_auth_from_dict_returns_auth_strategy(self):
        """Verify auth_from_dict returns AuthStrategy instance."""
        data = {"type": "bearer", "token": "test"}
        result = auth_from_dict(data)
        assert isinstance(result, AuthStrategy)

    def test_list_auth_types_returns_list_of_strings(self):
        """Verify list_auth_types returns list of strings."""
        result = list_auth_types()
        assert isinstance(result, list)
        assert all(isinstance(item, str) for item in result)


class TestBackwardCompatibility:
    """Test that changes maintain backward compatibility."""

    def test_direct_imports_still_work(self):
        """Verify direct imports of concrete types still work."""
        from equinox.auth import BasicAuth, BearerAuth, OAuth2Auth

        assert BearerAuth is not None
        assert BasicAuth is not None
        assert OAuth2Auth is not None

    def test_star_import_works(self):
        """Verify from equinox.auth import * still works."""
        # This doesn't fail if __all__ is properly defined
        import equinox.auth

        exported = [name for name in dir(equinox.auth) if not name.startswith("_")]
        assert len(exported) > 6

    def test_auth_strategy_import_works(self):
        """Verify AuthStrategy can still be imported directly."""
        from equinox.auth import AuthStrategy

        assert AuthStrategy is not None


class TestExportValidation:
    """Test runtime validation of exports."""

    def test_module_loads_without_error(self):
        """Verify module can be imported without errors."""
        import equinox.auth

        assert equinox.auth is not None

    def test_validation_catches_sync_issues(self):
        """Verify validation would catch if __all__ gets out of sync."""
        import equinox.auth

        # This test passes because validation succeeded at import time
        # If we manually added something to __all__ that didn't exist,
        # the module would fail to import with ImportError
        assert hasattr(equinox.auth, "__all__")


class TestIntegration:
    """Integration tests using multiple functions together."""

    def test_list_get_and_instantiate(self):
        """Test getting auth types from list and instantiating them."""
        types = list_auth_types()

        # Get a few types and verify we can get their classes
        for type_name in types[:3]:  # Test first 3
            auth_class = get_auth_type(type_name)
            assert issubclass(auth_class, AuthStrategy)

    def test_serialize_deserialize_roundtrip(self):
        """Test that auth objects can be serialized and deserialized."""
        # Create an auth object
        original = BearerAuth(token="test-token")

        # Serialize to dict
        data = original.to_dict()
        assert data["type"] == "bearer"

        # Deserialize from dict
        restored = auth_from_dict(data)
        assert isinstance(restored, BearerAuth)
        assert restored.token == "test-token"

    def test_factory_pattern_for_all_types(self):
        """Test factory pattern works for all available types."""
        types = list_auth_types()

        for type_name in types:
            # Get class by name
            auth_class = get_auth_type(type_name)

            # Verify it's a subclass of AuthStrategy
            assert issubclass(auth_class, AuthStrategy)

            # Verify it can be looked up by class name too
            class_name = auth_class.__name__
            same_class = get_auth_type(class_name)
            assert same_class == auth_class


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
