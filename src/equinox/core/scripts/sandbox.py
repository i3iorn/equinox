import builtins
from typing import Any
from .models import ALLOWED_MODULES, ALLOWED_PARENT_PACKAGES

def _safe_import(
    name: str,
    globals: Any = None,
    locals: Any = None,
    fromlist: Any = (),
    level: int = 0,
) -> Any:
    if level != 0:
        raise ImportError("Relative imports are not allowed in scripts")

    if name in ALLOWED_MODULES:
        return __import__(name, globals, locals, fromlist, level)

    if name in ALLOWED_PARENT_PACKAGES and fromlist:
        normalized = [item for item in fromlist if isinstance(item, str)]
        if normalized and all(f"{name}.{item}" in ALLOWED_MODULES for item in normalized):
            return __import__(name, globals, locals, fromlist, level)

    raise ImportError(f"Module '{name}' is not allowed in scripts")

_BLOCKED = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "__loader__",
        "__spec__",
        "__build_class__",
        "getattr",
        "setattr",   # must be blocked alongside delattr — AST check only catches direct calls
        "hasattr",
        "delattr",
        "type",
        "vars",
        "globals",
        "locals",
        "help",
    }
)

def get_safe_builtins() -> dict:
    safe_builtins = {
        name: getattr(builtins, name)
        for name in dir(builtins)
        if name not in _BLOCKED and not name.startswith("_")
    }
    safe_builtins["__import__"] = _safe_import
    # print is a no-op in the sandbox; ScriptRunner captures sys.stdout
    safe_builtins["print"] = lambda *args, **kwargs: None
    return safe_builtins
