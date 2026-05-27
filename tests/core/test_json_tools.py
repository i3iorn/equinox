from __future__ import annotations

import pytest

from equinox.core.exceptions import JsonParseError, SecurityError
from equinox.core.json_tools import (
    JsonDecoder,
    JsonLexer,
    LexerState,
    json_to_object,
    json_to_str,
    safe_json_dumps,
    sax_events,
    stream_json_objects,
    strip_json_comments,
)
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
