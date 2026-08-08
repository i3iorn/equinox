"""Tests for variable interpolation"""

from unittest.mock import MagicMock, patch

import pytest

from equinox.core.exceptions import SecurityError, ValidationError
from equinox.core.interpolation import (
    VariableInterpolator,
    collect_interpolation_variables_detailed,
)
from equinox.core.request import Request


class TestBasicInterpolation:
    """Test basic variable interpolation"""

    def test_simple_variable_replacement(self):
        """Test simple variable replacement"""
        text = "Hello {{name}}!"
        variables = {"name": "World"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "Hello World!"

    def test_multiple_variables(self):
        """Test multiple variables in text"""
        text = "{{protocol}}://{{host}}:{{port}}/{{path}}"
        variables = {"protocol": "https", "host": "api.example.com", "port": "443", "path": "users"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "https://api.example.com:443/users"

    def test_variable_not_found(self):
        """Test that missing variables are left unchanged"""
        text = "{{found}} and {{notfound}}"
        variables = {"found": "value"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "value and {{notfound}}"

    def test_empty_text(self):
        """Test empty text returns empty"""
        result = VariableInterpolator.interpolate("", {"key": "value"})
        assert result == ""

    def test_empty_variables(self):
        """Test empty variables returns unchanged text"""
        text = "{{variable}}"
        result = VariableInterpolator.interpolate(text, {})
        assert result == "{{variable}}"

    def test_no_variables_in_text(self):
        """Test text without variables"""
        text = "plain text without variables"
        result = VariableInterpolator.interpolate(text, {"key": "value"})
        assert result == text

    def test_variable_with_underscore(self):
        """Test variable names with underscores"""
        text = "{{api_key}}"
        variables = {"api_key": "secret123"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "secret123"

    def test_variable_with_hyphen(self):
        """Test variable names with hyphens"""
        text = "{{user-id}}"
        variables = {"user-id": "12345"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "12345"

    def test_variable_with_numbers(self):
        """Test variable names with numbers"""
        text = "{{server1}} and {{server2}}"
        variables = {"server1": "prod", "server2": "staging"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "prod and staging"

    def test_duplicate_variables(self):
        """Test same variable appears multiple times"""
        text = "{{var}} {{var}} {{var}}"
        variables = {"var": "value"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "value value value"


class TestNestedInterpolation:
    """Test nested/recursive variable interpolation"""

    def test_nested_variables(self):
        """Test variables that reference other variables"""
        text = "{{url}}"
        variables = {"url": "{{protocol}}://{{host}}", "protocol": "https", "host": "example.com"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "https://example.com"

    def test_max_iterations_warning(self):
        """Test that max iterations is respected"""
        text = "{{a}}"
        variables = {"a": "{{b}}", "b": "{{c}}", "c": "{{d}}", "d": "value"}
        # With default max_iterations=10, should complete
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "value"

    def test_circular_reference_protection(self):
        """Test circular reference doesn't cause infinite loop"""
        text = "{{a}}"
        variables = {"a": "{{b}}", "b": "{{a}}"}
        # Should stop at max iterations
        result = VariableInterpolator.interpolate(text, variables, max_iterations=5)
        # Will still have unresolved variables
        assert "{{" in result


class TestSecurityValidation:
    """Test security validations"""

    def test_invalid_text_type(self):
        """Test non-string text raises error"""
        with pytest.raises(ValidationError, match="must be a string"):
            VariableInterpolator.interpolate(123, {})

    def test_invalid_variables_type(self):
        """Test non-dict variables raises error"""
        with pytest.raises(ValidationError, match="must be a dictionary"):
            VariableInterpolator.interpolate("text", "not-a-dict")

    def test_excessive_expansion_protection(self):
        """Test protection against DoS via expansion"""
        text = "{{expand}}"
        # Create a variable that expands to 1000 characters
        variables = {"expand": "x" * 1000}

        # First expansion should work
        result = VariableInterpolator.interpolate(text, variables)
        assert len(result) == 1000

        # But if we nest it to cause massive expansion
        text = "{{a}}"
        variables = {"a": "{{b}}" * 50, "b": "x" * 100}

        with pytest.raises(SecurityError, match="excessive text expansion"):
            VariableInterpolator.interpolate(text, variables)


class TestFindVariables:
    """Test finding variables in text"""

    def test_find_single_variable(self):
        """Test finding single variable"""
        text = "Hello {{name}}"
        variables = VariableInterpolator.find_variables(text)
        assert variables == ["name"]

    def test_find_multiple_variables(self):
        """Test finding multiple variables"""
        text = "{{protocol}}://{{host}}/{{path}}"
        variables = VariableInterpolator.find_variables(text)
        assert set(variables) == {"protocol", "host", "path"}

    def test_find_duplicate_variables(self):
        """Test duplicates are removed"""
        text = "{{var}} {{var}} {{var}}"
        variables = VariableInterpolator.find_variables(text)
        assert variables == ["var"]

    def test_find_no_variables(self):
        """Test text without variables"""
        text = "plain text"
        variables = VariableInterpolator.find_variables(text)
        assert variables == []

    def test_find_variables_invalid_type(self):
        """Test non-string returns empty list"""
        variables = VariableInterpolator.find_variables(123)
        assert variables == []


class TestHasVariables:
    """Test checking for variables"""

    def test_has_variables_true(self):
        """Test detecting variables"""
        assert VariableInterpolator.has_variables("{{var}}")
        assert VariableInterpolator.has_variables("text {{var}} more")
        assert VariableInterpolator.has_variables("{{a}} {{b}}")

    def test_has_variables_false(self):
        """Test no variables"""
        assert not VariableInterpolator.has_variables("plain text")
        assert not VariableInterpolator.has_variables("")
        assert not VariableInterpolator.has_variables("{ not a var }")

    def test_has_variables_invalid_type(self):
        """Test non-string returns False"""
        assert not VariableInterpolator.has_variables(123)
        assert not VariableInterpolator.has_variables(None)


class TestRequestInterpolation:
    """Test interpolating variables in Request objects"""

    def test_interpolate_url(self):
        """Test URL interpolation"""
        request = Request(method="GET", url="{{protocol}}://{{host}}/{{path}}")
        variables = {"protocol": "https", "host": "api.example.com", "path": "users"}

        request = VariableInterpolator.interpolate_request(request, variables)
        assert request.url == "https://api.example.com/users"

    def test_interpolate_headers(self):
        """Test headers interpolation"""
        request = Request(
            method="GET",
            url="https://api.example.com",
            headers={"Authorization": "Bearer {{token}}", "X-API-Key": "{{api_key}}"},
        )
        variables = {"token": "abc123", "api_key": "secret"}

        request = VariableInterpolator.interpolate_request(request, variables)
        assert request.headers["Authorization"] == "Bearer abc123"
        assert request.headers["X-API-Key"] == "secret"

    def test_interpolate_params(self):
        """Test query params interpolation"""
        request = Request(
            method="GET",
            url="https://api.example.com",
            params={"user_id": "{{user}}", "limit": "{{limit}}"},
        )
        variables = {"user": "12345", "limit": "10"}

        request = VariableInterpolator.interpolate_request(request, variables)
        assert request.params["user_id"] == "12345"
        assert request.params["limit"] == "10"

    def test_interpolate_body(self):
        """Test body interpolation"""
        request = Request(
            method="POST",
            url="https://api.example.com",
            body='{"name": "{{name}}", "email": "{{email}}"}',
        )
        variables = {"name": "John Doe", "email": "john@example.com"}

        request = VariableInterpolator.interpolate_request(request, variables)
        assert request.body == '{"name": "John Doe", "email": "john@example.com"}'

    def test_interpolate_name(self):
        """Test request name interpolation"""
        request = Request(method="GET", url="https://api.example.com", name="Get {{resource}}")
        variables = {"resource": "users"}

        request = VariableInterpolator.interpolate_request(request, variables)
        assert request.name == "Get users"

    def test_interpolate_description(self):
        """Test request description interpolation"""
        request = Request(
            method="GET",
            url="https://api.example.com",
            description="Fetch {{resource}} from {{env}}",
        )
        variables = {"resource": "data", "env": "production"}

        request = VariableInterpolator.interpolate_request(request, variables)
        assert request.description == "Fetch data from production"

    def test_interpolate_all_fields(self):
        """Test interpolating all request fields at once"""
        request = Request(
            method="POST",
            url="{{base_url}}/{{endpoint}}",
            headers={"Authorization": "Bearer {{token}}"},
            params={"env": "{{environment}}"},
            body='{"key": "{{value}}"}',
            name="Create {{resource}}",
            description="Create {{resource}} in {{environment}}",
        )
        variables = {
            "base_url": "https://api.example.com",
            "endpoint": "items",
            "token": "secret123",
            "environment": "staging",
            "value": "test",
            "resource": "item",
        }

        request = VariableInterpolator.interpolate_request(request, variables)

        assert request.url == "https://api.example.com/items"
        assert request.headers["Authorization"] == "Bearer secret123"
        assert request.params["env"] == "staging"
        assert request.body == '{"key": "test"}'
        assert request.name == "Create item"
        assert request.description == "Create item in staging"

    def test_interpolate_empty_variables(self):
        """Test that empty variables doesn't change request"""
        original_url = "https://api.example.com"
        request = Request(method="GET", url=original_url)

        request = VariableInterpolator.interpolate_request(request, {})
        assert request.url == original_url

    def test_interpolate_none_values(self):
        """Test handling None values in request"""
        request = Request(
            method="GET",
            url="{{base_url}}/test",
            headers=None,
            params=None,
            body=None,
            name=None,
            description=None,
        )
        variables = {"base_url": "https://api.example.com"}

        # Should not raise errors
        request = VariableInterpolator.interpolate_request(request, variables)
        assert request.url == "https://api.example.com/test"


class TestEdgeCases:
    """Test edge cases and corner cases"""

    def test_adjacent_variables(self):
        """Test variables right next to each other"""
        text = "{{var1}}{{var2}}"
        variables = {"var1": "Hello", "var2": "World"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "HelloWorld"

    def test_variable_at_start(self):
        """Test variable at start of text"""
        text = "{{var}} text"
        variables = {"var": "Start"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "Start text"

    def test_variable_at_end(self):
        """Test variable at end of text"""
        text = "text {{var}}"
        variables = {"var": "End"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "text End"

    def test_only_variable(self):
        """Test text that is only a variable"""
        text = "{{var}}"
        variables = {"var": "value"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "value"

    def test_incomplete_variable_syntax(self):
        """Test incomplete variable syntax is not matched"""
        text = "{{incomplete} or {complete}}"
        variables = {"incomplete": "value", "complete": "value"}
        result = VariableInterpolator.interpolate(text, variables)
        # Neither should match as they don't have proper {{}} syntax
        assert result == text

    def test_empty_variable_name(self):
        """Test empty variable name is not matched"""
        text = "{{}}"
        variables = {"": "value"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "{{}}"  # Empty name doesn't match pattern

    def test_variable_with_spaces(self):
        """Test variable names cannot have spaces"""
        text = "{{var name}}"
        variables = {"var name": "value"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "{{var name}}"  # Doesn't match pattern

    def test_special_characters_in_replacement(self):
        """Test replacement value with special characters"""
        text = "{{var}}"
        variables = {"var": "value with $pecial ch@rs!"}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "value with $pecial ch@rs!"

    def test_empty_string_replacement(self):
        """Test variable replaced with empty string"""
        text = "prefix{{var}}suffix"
        variables = {"var": ""}
        result = VariableInterpolator.interpolate(text, variables)
        assert result == "prefixsuffix"

    def test_case_insensitive_fallback_when_single_casing_exists(self):
        """Exact case remains preferred, but single-case definitions can still resolve."""
        result = VariableInterpolator.interpolate(
            "https://{{BASE_URL}}/health",
            {"base_url": "api.example.com"},
        )
        assert result == "https://api.example.com/health"

    def test_case_insensitive_fallback_preserves_ambiguous_collisions(self):
        """When two values differ only by case, unresolved placeholder is safer."""
        result = VariableInterpolator.interpolate(
            "{{foo}}/{{FOO}}/{{FoO}}",
            {"foo": "a", "FOO": "b"},
        )
        assert result == "a/b/{{FoO}}"


class TestCollectInterpolationVariables:
    """Tests for source-aware variable collection and precedence."""

    def test_os_vars_do_not_override_collection_vars(self, monkeypatch):
        monkeypatch.setenv("BASE_URL", "https://os.example")
        db = MagicMock()

        with patch("equinox.storage.environments.EnvironmentManager") as mock_env:
            mock_env.return_value.get_active_environment.return_value = None
            with patch("equinox.storage.collections.CollectionManager") as mock_col:
                mock_col.return_value.get_all_collection_variables.return_value = {
                    "BASE_URL": "https://collection.example",
                }
                variables, sources = collect_interpolation_variables_detailed(db, collection_id=25)

        assert variables["BASE_URL"] == "https://collection.example"
        assert sources["BASE_URL"] == "collection"

    def test_session_vars_override_and_are_tagged_as_session(self):
        db = MagicMock()

        with patch("equinox.storage.environments.EnvironmentManager") as mock_env:
            mock_env.return_value.get_active_environment.return_value = {
                "variables": {"BASE_URL": "https://env.example"},
            }
            variables, sources = collect_interpolation_variables_detailed(
                db,
                session_vars={"BASE_URL": "https://session.example"},
            )

        assert variables["BASE_URL"] == "https://session.example"
        assert sources["BASE_URL"] == "session"

    def test_collection_non_string_values_are_coerced_for_interpolation(self):
        db = MagicMock()

        with patch("equinox.storage.environments.EnvironmentManager") as mock_env:
            mock_env.return_value.get_active_environment.return_value = None
            with patch("equinox.storage.collections.CollectionManager") as mock_col:
                mock_col.return_value.get_all_collection_variables.return_value = {
                    "BASE_URL": 12345,
                }
                variables, sources = collect_interpolation_variables_detailed(db, collection_id=25)

        assert variables["BASE_URL"] == "12345"
        assert sources["BASE_URL"] == "collection"
        assert (
            VariableInterpolator.interpolate("https://{{BASE_URL}}/livez", variables)
            == "https://12345/livez"
        )

    def test_magic_variables_are_available(self):
        db = MagicMock()

        with patch("equinox.storage.environments.EnvironmentManager") as mock_env:
            mock_env.return_value.get_active_environment.return_value = None
            with patch("equinox.storage.global_variables.GlobalVariablesManager") as mock_global:
                mock_global.return_value.get_variables_dict.return_value = {}
                variables, sources = collect_interpolation_variables_detailed(db)

        assert "TODAY" in variables
        assert "ONE_MONTH_AGO" in variables
        assert "ONE_YEAR_AGO" in variables
        assert "NOW_ISO" in variables
        assert sources["TODAY"] == "magic"

    def test_global_variables_override_magic_defaults(self):
        db = MagicMock()

        with patch("equinox.storage.environments.EnvironmentManager") as mock_env:
            mock_env.return_value.get_active_environment.return_value = None
            with patch("equinox.storage.global_variables.GlobalVariablesManager") as mock_global:
                mock_global.return_value.get_variables_dict.return_value = {"TODAY": "2099-01-01"}
                variables, sources = collect_interpolation_variables_detailed(db)

        assert variables["TODAY"] == "2099-01-01"
        assert sources["TODAY"] == "global"
