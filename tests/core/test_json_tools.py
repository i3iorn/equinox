from __future__ import annotations

import pytest
from equinox.core.exceptions import JsonParseError
from equinox.core.exceptions import SecurityError
from equinox.core.json_tools import json_to_object
from equinox.core.json_tools import json_to_str
from equinox.core.json_tools import JsonDecoder
from equinox.core.json_tools import JsonLexer
from equinox.core.json_tools import LexerState
from equinox.core.json_tools import safe_json_dumps
from equinox.core.json_tools import sax_events
from equinox.core.json_tools import stream_json_objects
from equinox.core.json_tools import strip_json_comments
from equinox.core.json_tools.lexer import InvalidLexerStateError
from equinox.core.json_tools.traversal import iter_json_lines
from equinox.core.json_tools.utils import strip_tags
from equinox.core.json_tools.utils import tag_value
from equinox.core.json_tools.utils import walk
from equinox.core.json_tools.validation import JsonConversionError


def test_strip_json_comments_preserves_strings() -> None:
    text = '{"url": "https://example.com//still-string", /* block */ "value": 1}'

    stripped = strip_json_comments(text)

    assert "block" not in stripped
    assert "https://example.com//still-string" in stripped


def test_json_decoder_loads_jsonc_with_comments() -> None:
    decoder = JsonDecoder(allow_comments=True)
    payload = decoder.loads_jsonc('{"name": "demo", // trailing\n "enabled": true}')

    assert payload == {"name": "demo", "enabled": True}


def test_json_decoder_rejects_unterminated_block_comment() -> None:
    decoder = JsonDecoder(allow_comments=True)

    with pytest.raises(JsonParseError):
        decoder.loads_jsonc('{"name": 1 /* broken')


def test_json_lexer_supports_legacy_keyword_arguments() -> None:
    lexer = JsonLexer(allow_comments=False, detect_timestamps=True)
    tokens, state = lexer.tokenize_line('"2026-03-24T20:16:59.114824"', LexerState.NORMAL)

    assert state == LexerState.NORMAL
    assert [token.type for token in tokens] == ["TIMESTAMP"]


def test_json_lexer_marks_unterminated_string_at_document_end() -> None:
    lexer = JsonLexer()

    tokens = list(lexer.tokenize('{"name": "broken}'))

    assert any(token.type == "ERROR_STRING" for token in tokens)


def test_json_to_object_streaming_and_limits() -> None:
    result = json_to_object('{"a": 1}\n{"b": 2}', streaming=True, max_depth=3)

    assert result.value == [{"a": 1}, {"b": 2}]


def test_json_to_object_schema_validation_failure_is_wrapped() -> None:
    with pytest.raises(JsonConversionError):
        json_to_object(
            '{"count": "bad"}',
            schema={"type": "object", "properties": {"count": {"type": "integer"}}},
        )


def test_json_to_str_honors_max_length() -> None:
    with pytest.raises(JsonConversionError):
        json_to_str({"message": "hello"}, max_length=5)


def test_safe_json_dumps_rejects_negative_max_len() -> None:
    with pytest.raises(ValueError):
        safe_json_dumps({"ok": True}, max_len=-1)


def test_safe_json_dumps_enforces_output_limit() -> None:
    with pytest.raises(SecurityError):
        safe_json_dumps({"value": "x" * 20}, max_len=10)


def test_stream_json_objects_and_sax_events() -> None:
    objects = list(stream_json_objects('{"a": 1}\n[true, false]'))
    events = list(sax_events(objects[0]))

    assert objects == [{"a": 1}, [True, False]]
    assert events[:4] == [
        ("start_object", None),
        ("key", "a"),
        ("value", 1),
        ("end_object", None),
    ]


def test_json_utils_tag_strip_and_walk_nested_structures() -> None:
    tagged = {
        "token": tag_value("abc123", "secret"),
        "items": [tag_value(1, "num"), {"enabled": tag_value(True, "bool")}],
    }

    assert strip_tags(tagged) == {
        "token": "abc123",
        "items": [1, {"enabled": True}],
    }

    visited = list(walk({"top": 1, "nested": {"leaf": 2}, "arr": [{"k": 3}]}))
    assert ("top", 1) in visited
    assert ("leaf", 2) in visited
    assert ("k", 3) in visited


def test_iter_json_lines_ignores_blank_lines() -> None:
    text = ' \n{"a":1}\n\n[1,2]\n'
    assert list(iter_json_lines(text)) == ['{"a":1}', "[1,2]"]


def test_lexer_state_conversion_rejects_invalid_values() -> None:
    assert LexerState.from_string("normal") == LexerState.NORMAL
    assert LexerState.from_int(0) == LexerState.NORMAL
    assert LexerState.from_int(1) == LexerState.STRING
    assert LexerState.from_int(2) == LexerState.COMMENT_BLOCK

    with pytest.raises(InvalidLexerStateError):
        LexerState.from_string("bad-state")

    with pytest.raises(InvalidLexerStateError):
        LexerState.from_int(99)


def test_json_lexer_rejects_invalid_input_types_and_state_types() -> None:
    lexer = JsonLexer()

    with pytest.raises(TypeError):
        list(lexer.tokenize(123))  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        lexer.tokenize_line(123, LexerState.NORMAL)  # type: ignore[arg-type]

    with pytest.raises(InvalidLexerStateError):
        lexer.tokenize_line("{}", object())


def test_json_lexer_emits_error_tokens_for_string_edge_cases() -> None:
    lexer = JsonLexer()

    unterminated_escape = '"bad\\'
    tokens, _state = lexer.tokenize_line(unterminated_escape, LexerState.NORMAL)
    assert any(token.type == "ERROR_STRING" for token in tokens)

    tokens, _state = lexer.tokenize_line('"\\u12GZ"', LexerState.NORMAL)
    assert any(token.type == "ERROR_STRING" for token in tokens)

    tokens, _state = lexer.tokenize_line('"\\x"', LexerState.NORMAL)
    assert any(token.type == "ERROR_STRING" for token in tokens)

    tokens, _state = lexer.tokenize_line('"\x01"', LexerState.NORMAL)
    assert any(token.type == "ERROR_STRING" for token in tokens)


def test_json_lexer_handles_literals_comments_numbers_and_eof_comment_errors() -> None:
    lexer = JsonLexer()

    tokens = list(lexer.tokenize("true false null 123 @ // trailing"))
    token_types = [token.type for token in tokens]
    assert "TRUE" in token_types
    assert "FALSE" in token_types
    assert "NULL" in token_types
    assert "NUMBER" in token_types
    assert "ERROR" in token_types
    assert "COMMENT" in token_types

    eof_tokens = list(lexer.tokenize("/* unterminated"))
    assert any(token.type == "ERROR_COMMENT" for token in eof_tokens)


def test_json_to_object_validation_and_parse_failures_are_wrapped() -> None:
    with pytest.raises(JsonConversionError, match="Input must be a string"):
        json_to_object(123)  # type: ignore[arg-type]

    with pytest.raises(JsonConversionError, match="empty"):
        json_to_object("   ")

    with pytest.raises(JsonConversionError, match="maximum allowed length"):
        json_to_object('{"a": 1}', max_length=3)

    with pytest.raises(JsonConversionError, match="Failed to parse JSON input"):
        json_to_object('{"a": 1,,}')


def test_json_to_str_wraps_serialization_failures() -> None:
    with pytest.raises(JsonConversionError, match="Failed to serialize object to JSON"):
        json_to_str({"bad": object()})
