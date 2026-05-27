from equinox.core.json_tools import JsonLexerConfig
from equinox.gui.syntax_highlighter.json_highlighter import JsonLexer, LexerState


def test_timestamp_tokenized() -> None:
    lexer = JsonLexer(JsonLexerConfig(allow_comments=False, detect_timestamps=True))
    line = '"2026-03-24T20:16:59.114824"\n'
    tokens, normalized_state = list(lexer.tokenize_line(line, LexerState.NORMAL))

    # Expect a TIMESTAMP token spanning the quoted string
    types = [t.type for t in tokens]
    assert "TIMESTAMP" in types, f"Expected TIMESTAMP in {types}"

    # Find the TIMESTAMP token and verify its value (unquoted content)
    ts_tokens = [t for t in tokens if t.type == "TIMESTAMP"]
    assert len(ts_tokens) == 1
    assert ts_tokens[0].value == "2026-03-24T20:16:59.114824"
