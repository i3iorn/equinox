from __future__ import annotations

import json
from pathlib import Path

from ..exceptions import JsonParseError
from .lexer import JsonLexer


class JsonDecoder:
    def __init__(self, *, allow_comments=False):
        self.lexer = JsonLexer(allow_comments=allow_comments)

    def loads(self, text: str):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise JsonParseError(str(exc)) from exc

    def loads_strict(self, text: str):
        tokens = list(self.lexer.tokenize(text))
        if any(t.type.startswith("ERROR") for t in tokens):
            raise JsonParseError("Invalid JSON structure")
        return json.loads(text)

    def load_file(self, path: Path):
        return self.loads(path.read_text(encoding="utf-8"))

    def loads_jsonc(self, text: str):
        cleaned = []
        for tok in self.lexer.tokenize(text):
            if tok.type != "COMMENT":
                cleaned.append(tok.value if tok.type not in "{}[]:," else tok.type)
        return json.loads("".join(cleaned))
