import builtins
import types
from typing import Any

from .models import ALLOWED_MODULES, ALLOWED_PARENT_PACKAGES

# Attribute names never exposed on a sandboxed module, whatever they hold.
#
# Allowlisting a module is not the same as allowlisting everything it happens
# to re-export. ``codecs.open`` is the concrete case: ``open`` is removed from
# builtins, but codecs offers its own, which reads and writes arbitrary files.
_FORBIDDEN_MODULE_ATTRS: frozenset[str] = frozenset(
    {
        "open",
        "system",
        "popen",
        "exec",
        "execv",
        "execve",
        "spawn",
        "spawnl",
        "spawnle",
        "fork",
        "kill",
        "remove",
        "unlink",
        "rmdir",
        "rename",
        "chmod",
        "environ",
        "getenv",
        "putenv",
    },
)


class _SafeModule:
    """A module wrapper that refuses to hand out other modules.

    The import allowlist governs which modules a script may *import*, but not
    what those modules *re-export*. Several stdlib modules keep a plain
    reference to a dangerous one — ``uuid.os``, ``random._os``,
    ``codecs.builtins``, ``collections._sys``, ``pprint._sys`` — so an
    allowlisted import was enough to reach ``os.system``, real ``eval``, or
    ``sys.modules``, defeating both the blocked builtins and the AST checks.

    Enumerating those five would not hold: any stdlib change can add another.
    This blocks the shape of the escape instead — an attribute that resolves
    to a module is refused unless that module is itself allowlisted.
    """

    __slots__ = ("_wrapped",)

    def __init__(self, module: types.ModuleType) -> None:
        object.__setattr__(self, "_wrapped", module)

    def __repr__(self) -> str:
        wrapped: types.ModuleType = object.__getattribute__(self, "_wrapped")
        return f"<sandboxed module {wrapped.__name__!r}>"

    def __getattr__(self, name: str) -> Any:
        wrapped: types.ModuleType = object.__getattribute__(self, "_wrapped")
        if name in _FORBIDDEN_MODULE_ATTRS:
            raise AttributeError(
                f"'{wrapped.__name__}.{name}' is not available in scripts",
            )
        value = getattr(wrapped, name)
        if isinstance(value, types.ModuleType):
            if value.__name__ in ALLOWED_MODULES:
                return _SafeModule(value)
            raise AttributeError(
                f"'{wrapped.__name__}.{name}' exposes module "
                f"'{value.__name__}', which is not allowed in scripts",
            )
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Sandboxed modules are read-only")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Sandboxed modules are read-only")


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
        return _SafeModule(__import__(name, globals, locals, fromlist, level))

    if name in ALLOWED_PARENT_PACKAGES and fromlist:
        normalized = [item for item in fromlist if isinstance(item, str)]
        if normalized and all(f"{name}.{item}" in ALLOWED_MODULES for item in normalized):
            return _SafeModule(__import__(name, globals, locals, fromlist, level))

    raise ImportError(f"Module '{name}' is not allowed in scripts")


_BLOCKED: frozenset[str] = frozenset(
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
        "setattr",  # must be blocked alongside delattr — AST check only catches direct calls
        "hasattr",
        "delattr",
        "type",
        "vars",
        "globals",
        "locals",
        "help",
    },
)


def get_safe_builtins() -> dict[str, Any]:
    safe_builtins: dict[str, Any] = {
        name: getattr(builtins, name)
        for name in dir(builtins)
        if name not in _BLOCKED and not name.startswith("_")
    }
    safe_builtins["__import__"] = _safe_import
    # print is a no-op in the sandbox; ScriptRunner captures sys.stdout
    safe_builtins["print"] = lambda *args, **kwargs: None
    return safe_builtins
