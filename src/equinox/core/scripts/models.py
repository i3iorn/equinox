from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScriptResult:
    """Outcome of a script execution."""

    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    env_changes: dict[str, str] = field(default_factory=dict)
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "json",
        "re",
        "base64",
        "hashlib",
        "hmac",
        "time",
        "math",
        "random",
        "urllib.parse",
        "collections",
        "itertools",
        "functools",
        "string",
        "datetime",
        "uuid",
        "decimal",
        "struct",
        "binascii",
        "codecs",
        "textwrap",
        "pprint",
    },
)

ALLOWED_PARENT_PACKAGES: frozenset[str] = frozenset({"urllib"})
