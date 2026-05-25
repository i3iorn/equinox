import ast

from equinox.core.exceptions import SecurityError

_DANGEROUS_ATTRS = frozenset(
    {
        "__class__",
        "__mro__",
        "__bases__",
        "__subclasses__",
        "__builtins__",
        "__globals__",
        "__code__",
        "__func__",
        "__self__",
        "__dict__",
        "__getattribute__",
        "__setattr__",
        "__delattr__",
        "__init_subclass__",
        "__reduce__",
        "__reduce_ex__",
    }
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
        }
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in _DANGEROUS_ATTRS:
                raise SecurityError(
                    f"Access to '{node.attr}' is blocked in scripts "
                    f"(line {getattr(node, 'lineno', '?')})"
                )
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                # Block 3-arg type() — dynamic class creation (sandbox escape)
                if func.id == "type" and len(node.args) == 3:
                    raise SecurityError(
                        f"type() with 3 arguments (class creation) is blocked in scripts "
                        f"(line {getattr(node, 'lineno', '?')})"
                    )
                # Block introspection / attribute manipulation builtins
                if func.id in _BLOCKED_CALLS:
                    raise SecurityError(
                        f"'{func.id}()' is blocked in scripts "
                        f"(line {getattr(node, 'lineno', '?')})"
                    )
                # Block getattr with dangerous attr names
                if func.id == "getattr" and len(node.args) >= 2:
                    arg = node.args[1]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if arg.value in _DANGEROUS_ATTRS:
                            raise SecurityError(
                                f"getattr() with '{arg.value}' is blocked in scripts "
                                f"(line {getattr(node, 'lineno', '?')})"
                            )

    return tree
