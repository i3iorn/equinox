import ast

from equinox.core.exceptions import SecurityError

_DANGEROUS_ATTRS = frozenset(
    {
        # Class / type traversal. ``__base__`` matters as much as ``__bases__``:
        # ``list.__base__.__subclasses__()`` reaches every loaded class.
        "__class__",
        "__base__",
        "__bases__",
        "__mro__",
        "__subclasses__",
        # Namespaces. These hand back the *real* module globals, whose
        # ``__builtins__`` is the unrestricted one — not the sandbox's.
        "__builtins__",
        "__globals__",
        "__dict__",
        "__closure__",
        "__defaults__",
        "__kwdefaults__",
        # Callables / bound methods.
        "__code__",
        "__func__",
        "__self__",
        "__wrapped__",
        # Frame and traceback introspection. ``tb_frame`` reaches a live frame,
        # and ``f_builtins``/``f_globals`` on a stdlib frame expose the real
        # builtins — so an exception raised inside ``json`` is enough to escape.
        "__traceback__",
        "__context__",
        "__cause__",
        "tb_frame",
        "tb_next",
        "tb_lasti",
        "f_back",
        "f_builtins",
        "f_globals",
        "f_locals",
        "f_code",
        "f_trace",
        # Generator / coroutine / async-generator frames — same trick, no ``try``.
        "gi_frame",
        "gi_code",
        "cr_frame",
        "cr_code",
        "ag_frame",
        "ag_code",
        # Module introspection. ``__loader__``/``__spec__`` yield importlib
        # loader objects whose ``get_data(path)`` reads any file on disk.
        "__loader__",
        "__spec__",
        "__cached__",
        "__file__",
        "__path__",
        "__package__",
        # Attribute manipulation.
        "__getattribute__",
        "__setattr__",
        "__delattr__",
        "__init_subclass__",
        "__reduce__",
        "__reduce_ex__",
    },
)


def _validate_ast(source: str, filename: str) -> ast.Module:
    """Parse *source* and reject dangerous attribute access patterns."""
    try:
        tree = ast.parse(source, filename=filename, mode="exec")
    except RecursionError as exc:
        raise SyntaxError("Script is too deeply nested to parse safely") from exc

    # Names that are blocked when used as function calls
    _BLOCKED_CALLS: frozenset[str] = frozenset(
        {
            "setattr",
            "delattr",
            "vars",
            "globals",
            "locals",
            "classmethod",
            "staticmethod",
            "property",
            "super",
        },
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in _DANGEROUS_ATTRS:
                raise SecurityError(
                    f"Access to '{node.attr}' is blocked in scripts "
                    f"(line {getattr(node, 'lineno', '?')})",
                )
        # ``from module import __loader__`` is not an ``ast.Attribute``, so the
        # check above never sees it — yet the import binds the same dangerous
        # object (``__loader__.get_data`` reads arbitrary files).
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _DANGEROUS_ATTRS:
                    raise SecurityError(
                        f"Importing '{alias.name}' is blocked in scripts "
                        f"(line {getattr(node, 'lineno', '?')})",
                    )
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                # Block 3-arg type() — dynamic class creation (sandbox escape)
                if func.id == "type" and len(node.args) == 3:
                    raise SecurityError(
                        f"type() with 3 arguments (class creation) is blocked in scripts "
                        f"(line {getattr(node, 'lineno', '?')})",
                    )
                # Block introspection / attribute manipulation builtins
                if func.id in _BLOCKED_CALLS:
                    raise SecurityError(
                        f"'{func.id}()' is blocked in scripts (line {getattr(node, 'lineno', '?')})",
                    )
                # Block getattr with dangerous attr names
                if func.id == "getattr" and len(node.args) >= 2:
                    arg = node.args[1]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if arg.value in _DANGEROUS_ATTRS:
                            raise SecurityError(
                                f"getattr() with '{arg.value}' is blocked in scripts "
                                f"(line {getattr(node, 'lineno', '?')})",
                            )

    return tree
