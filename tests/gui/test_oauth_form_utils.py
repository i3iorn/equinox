from __future__ import annotations

from equinox.gui.dialogs._oauth_form_utils import (
    parse_json_object_field,
    parse_json_object_field_lenient,
)


def test_parse_json_object_field_accepts_valid_object() -> None:
    value, error = parse_json_object_field('{"audience": "api"}')

    assert error is None
    assert value == {"audience": "api"}


def test_parse_json_object_field_rejects_non_object() -> None:
    value, error = parse_json_object_field('[1, 2, 3]')

    assert value is None
    assert "valid JSON object" in (error or "")


def test_parse_json_object_field_lenient_returns_empty_dict_for_invalid_input() -> None:
    assert parse_json_object_field_lenient('{bad json') == {}

