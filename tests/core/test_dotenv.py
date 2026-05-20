"""Tests for core/dotenv.py — .env file parser."""

import pytest

from equinox.core.io.dotenv import MAX_DOTENV_SIZE, parse_dotenv


class TestParseDotenv:
    """parse_dotenv() text → dict conversion."""

    def test_simple_key_value(self):
        assert parse_dotenv("KEY=value") == {"KEY": "value"}

    def test_multiple_entries(self):
        text = "A=1\nB=2\nC=3"
        assert parse_dotenv(text) == {"A": "1", "B": "2", "C": "3"}

    def test_blank_lines_ignored(self):
        text = "\nKEY=val\n\n"
        assert parse_dotenv(text) == {"KEY": "val"}

    def test_comment_lines_ignored(self):
        text = "# This is a comment\nKEY=val\n# Another comment"
        assert parse_dotenv(text) == {"KEY": "val"}

    def test_export_prefix_stripped(self):
        text = "export API_KEY=secret123"
        assert parse_dotenv(text) == {"API_KEY": "secret123"}

    def test_export_with_spaces(self):
        text = "export  DB_HOST=localhost"
        assert parse_dotenv(text) == {"DB_HOST": "localhost"}

    def test_double_quoted_value(self):
        text = 'KEY="hello world"'
        assert parse_dotenv(text) == {"KEY": "hello world"}

    def test_single_quoted_value(self):
        text = "KEY='hello world'"
        assert parse_dotenv(text) == {"KEY": "hello world"}

    def test_unquoted_value_with_spaces(self):
        # No stripping of inner spaces (quotes absent)
        text = "KEY=hello world"
        assert parse_dotenv(text) == {"KEY": "hello world"}

    def test_value_with_equals_sign(self):
        text = "DB_URL=postgres://user:pass@host/db?opt=val"
        result = parse_dotenv(text)
        assert result["DB_URL"] == "postgres://user:pass@host/db?opt=val"

    def test_empty_value(self):
        text = "EMPTY="
        assert parse_dotenv(text) == {"EMPTY": ""}

    def test_line_without_equals_ignored(self):
        text = "NO_EQUALS_HERE\nKEY=val"
        assert parse_dotenv(text) == {"KEY": "val"}

    def test_key_whitespace_trimmed(self):
        text = "  KEY  =value"
        assert parse_dotenv(text) == {"KEY": "value"}

    def test_value_whitespace_trimmed(self):
        text = "KEY=  value  "
        assert parse_dotenv(text) == {"KEY": "value"}

    def test_empty_key_ignored(self):
        text = "=value"
        assert parse_dotenv(text) == {}

    def test_empty_text(self):
        assert parse_dotenv("") == {}

    def test_only_comments_and_blanks(self):
        text = "# comment\n\n# another"
        assert parse_dotenv(text) == {}

    def test_mixed_realistic(self):
        text = """
# Database
DB_HOST=localhost
DB_PORT=5432
export DB_NAME="mydb"

# API
API_KEY='sk-abc-123'
DEBUG=true
"""
        result = parse_dotenv(text)
        assert result == {
            "DB_HOST": "localhost",
            "DB_PORT": "5432",
            "DB_NAME": "mydb",
            "API_KEY": "sk-abc-123",
            "DEBUG": "true",
        }

    def test_max_size_exceeded(self):
        text = "X" * (MAX_DOTENV_SIZE + 1)
        with pytest.raises(ValueError, match="maximum size"):
            parse_dotenv(text)

    def test_max_size_boundary_ok(self):
        # Exactly at limit should not raise
        text = "K=" + "v" * (MAX_DOTENV_SIZE - 3)
        assert len(text) <= MAX_DOTENV_SIZE
        result = parse_dotenv(text)
        assert "K" in result
