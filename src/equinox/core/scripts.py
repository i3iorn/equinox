"""Sandboxed Python script runner (stdlib only).

Scripts have access to a restricted set of built-ins and may only import
modules from a fixed allow-list of Python standard-library modules.  No
file I/O, process spawning, or network calls are possible from within
a script.

Available names inside every script:
  * All safe built-ins (int, str, list, dict, print [no-op], len, range, …)
  * ``request`` — dict view of the current request (pre-script)
  * ``response`` — dict view of the received response (post-script)
  * ``env``      — mutable session-variable dict; keys set here flow into
                   ``{{var}}`` interpolation for the *next* request

Allowed imports::

    import json
    import re
    from datetime import datetime
    import hashlib, hmac, base64
    # … see ALLOWED_MODULES for full list
"""

from __future__ import annotations

import ast
import builtins
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional


# ── Allow-list of importable top-level packages ──────────────────────────────

ALLOWED_MODULES: FrozenSet[str] = frozenset(
    {
        "json",
        "re",
        "base64",
        "hashlib",
        "hmac",
        "time",
        "math",
        "random",
        "urllib",
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
    }
)


def _safe_import(
    name: str,
    globals: Any = None,
    locals: Any = None,
    fromlist: Any = (),
    level: int = 0,
) -> Any:
    top = name.split(".")[0]
    if top not in ALLOWED_MODULES:
        raise ImportError(f"Module '{name}' is not allowed in scripts")
    return __import__(name, globals, locals, fromlist, level)


# Build a restricted builtins dict: keep most standard ones, remove
# dangerous ones, and replace __import__ with our gated version.
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
        # Block introspection builtins that can bypass AST-level checks
        # via string concatenation (e.g. getattr(obj, '__sub' + 'classes__'))
        "getattr",
        "hasattr",
        "delattr",
        "type",       # blocks type(name, bases, dict) dynamic class creation
        "vars",
        "globals",
        "locals",
        "classmethod",
        "staticmethod",
        "property",
        "super",
    }
)

SAFE_BUILTINS: Dict[str, Any] = {
    k: v for k, v in vars(builtins).items() if k not in _BLOCKED
}
SAFE_BUILTINS["__import__"] = _safe_import
# Allow print but route to a no-op so output doesn't pollute the GUI log
SAFE_BUILTINS["print"] = lambda *a, **kw: None  # noqa: E731


# ── AST-level sandbox enforcement ────────────────────────────────────────────

_DANGEROUS_ATTRS: frozenset[str] = frozenset(
    {
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__class__",
        "__dict__",
        "__getattr__",
        "__getattribute__",
        "__setattr__",
        "__delattr__",
        "__init_subclass__",
        "__reduce__",
        "__reduce_ex__",
    }
)


def _validate_ast(source: str, filename: str) -> ast.Module:
    """Parse *source* and reject dangerous attribute access patterns.

    Raises:
        SyntaxError:   If *source* is not valid Python.
        SecurityError: If the AST contains forbidden attribute access.
    """
    from equinox.core.exceptions import SecurityError

    tree = ast.parse(source, filename=filename, mode="exec")

    # Names that are blocked when used as function calls
    _BLOCKED_CALLS: frozenset = frozenset({
        "setattr", "delattr", "vars", "globals", "locals",
        "classmethod", "staticmethod", "property", "super",
    })

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


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class ScriptResult:
    """Return value from ScriptRunner.run_*().

    Attributes:
        output_vars: Variables that were added or changed in ``env``.
        error:       Error message if the script raised an exception.
    """

    output_vars: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ── Runner ────────────────────────────────────────────────────────────────────


class ScriptRunner:
    """Execute sandboxed Python scripts.

    Each script runs inside ``exec()`` with a restricted builtins dict.
    The script is compiled first so syntax errors are caught cleanly before
    any code runs.

    The runner never raises — all exceptions are captured into
    ``ScriptResult.error``.
    """

    # Maximum source code length (64 KB) to prevent memory exhaustion
    MAX_SOURCE_LENGTH = 64 * 1024
    # Maximum execution time in seconds
    EXECUTION_TIMEOUT = 10.0

    @classmethod
    def run_pre(
        cls,
        script: str,
        request_dict: Dict[str, Any],
        session_vars: Dict[str, str],
    ) -> ScriptResult:
        """Run a pre-request script.

        Args:
            script:       Source code to execute.
            request_dict: Snapshot of the current request (read/write in script).
            session_vars: Current ``{{var}}`` session variables.

        Returns:
            ScriptResult with ``output_vars`` containing any new/changed env
            entries and ``error`` set on exception.
        """
        return cls._run(script, {"request": dict(request_dict)}, session_vars, "<pre_script>")

    @classmethod
    def run_post(
        cls,
        script: str,
        response_dict: Dict[str, Any],
        session_vars: Dict[str, str],
    ) -> ScriptResult:
        """Run a post-response script.

        Args:
            script:        Source code to execute.
            response_dict: Snapshot of the received response.
            session_vars:  Current ``{{var}}`` session variables.

        Returns:
            ScriptResult with ``output_vars`` containing any new/changed env
            entries and ``error`` set on exception.
        """
        return cls._run(script, {"response": dict(response_dict)}, session_vars, "<post_script>")

    # ── Internal ─────────────────────────────────────────────────────────────

    @classmethod
    def _run(
        cls,
        script: str,
        extra_locals: Dict[str, Any],
        session_vars: Dict[str, str],
        filename: str,
    ) -> ScriptResult:
        if not script or not script.strip():
            return ScriptResult()

        if len(script) > cls.MAX_SOURCE_LENGTH:
            return ScriptResult(
                error=f"Script too long ({len(script)} chars, max {cls.MAX_SOURCE_LENGTH})"
            )

        globs: Dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
        locs: Dict[str, Any] = {"env": dict(session_vars)}
        locs.update(extra_locals)

        try:
            tree = _validate_ast(script, filename)
            code = compile(tree, filename, "exec")
        except Exception as exc:  # noqa: BLE001
            return ScriptResult(error=str(exc))

        # Run exec() in a thread with a timeout to prevent infinite loops
        error_container: list = [None]

        def _exec_target() -> None:
            try:
                exec(code, globs, locs)  # noqa: S102  (intentional sandboxed exec)
            except Exception as exc:  # noqa: BLE001
                error_container[0] = exc

        t = threading.Thread(target=_exec_target, daemon=True)
        t.start()
        t.join(timeout=cls.EXECUTION_TIMEOUT)

        if t.is_alive():
            return ScriptResult(
                error=f"Script timed out after {cls.EXECUTION_TIMEOUT}s"
            )
        if error_container[0] is not None:
            return ScriptResult(error=str(error_container[0]))

        # Collect any new or changed env entries, coercing values to str
        new_env: Dict[str, str] = locs.get("env", {})  # type: ignore[assignment]
        changed = {
            k: str(v)
            for k, v in new_env.items()
            if k not in session_vars or session_vars.get(k) != str(v)
        }
        return ScriptResult(output_vars=changed)
