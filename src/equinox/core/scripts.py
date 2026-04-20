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
import logging
import multiprocessing
import queue
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional

logger = logging.getLogger(__name__)


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

# Parent packages that may be imported only when fromlist is restricted to an
# explicitly allowed submodule (e.g. ``from urllib import parse``).
_ALLOWED_PARENT_PACKAGES: FrozenSet[str] = frozenset({"urllib"})


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

    if name in _ALLOWED_PARENT_PACKAGES and fromlist:
        normalized = [item for item in fromlist if isinstance(item, str)]
        if normalized and all(f"{name}.{item}" in ALLOWED_MODULES for item in normalized):
            return __import__(name, globals, locals, fromlist, level)

    raise ImportError(f"Module '{name}' is not allowed in scripts")


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

    try:
        tree = ast.parse(source, filename=filename, mode="exec")
    except RecursionError:
        # Deeply nested expressions (e.g. thousands of nested calls) hit
        # CPython's recursion limit during parsing before the AST walker runs.
        # Treat this as a syntax error so the result is a ScriptResult.error
        # rather than an unhandled exception propagating to the caller.
        raise SyntaxError("Script is too deeply nested to parse safely")

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


# ── Subprocess exec target ────────────────────────────────────────────────────

def _subprocess_exec_target(
    q: "multiprocessing.Queue[Any]",
    source: str,
    extra_locals: Dict[str, Any],
    session_vars: Dict[str, str],
    filename: str,
) -> None:
    """Top-level subprocess target for sandboxed script execution.

    **Must** be a module-level function — not a nested/local one — so that the
    'spawn' multiprocessing start method used on Windows can pickle it by
    qualified name (``equinox.core.scripts._subprocess_exec_target``).

    All arguments are plain picklable types (str / dict).  ``SAFE_BUILTINS``
    is *not* passed; it is reconstructed when this module is imported inside
    the child process.
    """
    try:
        globs: Dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
        locs: Dict[str, Any] = {"env": dict(session_vars)}
        locs.update(extra_locals)
        exec(compile(source, filename, "exec"), globs, locs)  # noqa: S102
        q.put(("ok", locs.get("env", {})))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", str(exc)))


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
    # Max number of changed env vars accepted from one script run
    MAX_OUTPUT_VARS = 128
    # Max UTF-8 bytes for all changed env keys+values combined
    MAX_OUTPUT_TOTAL_BYTES = 16 * 1024
    # Max key and value lengths for a single env entry
    MAX_ENV_KEY_LENGTH = 128
    MAX_ENV_VALUE_LENGTH = 4096

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
        logger.debug("Running pre-request script", extra={
            "script_length": len(script),
            "session_var_count": len(session_vars),
        })
        result = cls._run(script, {"request": dict(request_dict)}, session_vars, "<pre_script>")
        if result.error:
            logger.warning("Pre-request script failed: %s", result.error)
        else:
            logger.debug("Pre-request script completed", extra={
                "changed_vars": list(result.output_vars.keys()),
            })
        return result

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
        logger.debug("Running post-response script", extra={
            "script_length": len(script),
            "session_var_count": len(session_vars),
        })
        result = cls._run(script, {"response": dict(response_dict)}, session_vars, "<post_script>")
        if result.error:
            logger.warning("Post-response script failed: %s", result.error)
        else:
            logger.debug("Post-response script completed", extra={
                "changed_vars": list(result.output_vars.keys()),
            })
        return result

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
            logger.warning("Script rejected: too long (%d chars, max %d)", len(script), cls.MAX_SOURCE_LENGTH)
            return ScriptResult(
                error=f"Script too long ({len(script)} chars, max {cls.MAX_SOURCE_LENGTH})"
            )

        try:
            tree = _validate_ast(script, filename)
            # Compile once to surface syntax issues before process start.
            # Child process still compiles independently from source.
            compile(tree, filename, "exec")
            logger.debug("Script AST validation passed: %s", filename)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Script AST/compile error in %s: %s", filename, exc)
            return ScriptResult(error=str(exc))

        # Run exec() in a subprocess so infinite loops can be killed.
        # We use a multiprocessing.Queue to shuttle results / errors back.
        #
        # IMPORTANT: on Windows the 'spawn' start method requires every object
        # passed to the child process to be picklable.  That rules out:
        #   • Local/nested functions (no qualified __module__.__qualname__)
        #   • Lambdas (same reason)
        #   • Compiled code objects mixed with non-picklable builtins
        #
        # Solution: use the module-level ``_subprocess_exec_target`` and pass
        # only plain picklable types (str + dict).  The child process imports
        # this module and reconstructs SAFE_BUILTINS from scratch.

        try:
            ctx = multiprocessing.get_context("spawn")
            result_queue: multiprocessing.Queue = ctx.Queue()
            p = ctx.Process(
                target=_subprocess_exec_target,
                args=(result_queue, script, extra_locals, session_vars, filename),
                daemon=True,
            )
            p.start()
            logger.debug("Script subprocess started (pid=%s, filename=%s)", p.pid, filename)
            p.join(timeout=cls.EXECUTION_TIMEOUT)

            if p.is_alive():
                p.kill()
                p.join(timeout=2.0)
                logger.warning("Script timed out after %.1fs in %s", cls.EXECUTION_TIMEOUT, filename)
                return ScriptResult(
                    error=f"Script timed out after {cls.EXECUTION_TIMEOUT}s"
                )

            try:
                status, payload = result_queue.get(timeout=0.2)
            except queue.Empty:
                logger.warning("Script subprocess exited without a result (filename=%s)", filename)
                return ScriptResult(error="Script process exited without producing a result")

            if status == "error":
                logger.warning("Script runtime error in %s: %s", filename, payload)
                return ScriptResult(error=payload)

            new_env = payload
        except (OSError, RuntimeError) as exc:
            # Do not fall back to threads for untrusted scripts; a stuck thread
            # cannot be force-terminated safely and defeats timeout guarantees.
            logger.error("Process sandbox unavailable in %s: %s", filename, exc)
            return ScriptResult(error="Script sandbox unavailable: process isolation failed")

        # Collect any new or changed env entries, coercing values to str
        changed, limit_error = cls._collect_changed_env(new_env, session_vars)
        if limit_error is not None:
            logger.warning("Script env output rejected in %s: %s", filename, limit_error)
            return ScriptResult(error=limit_error)

        if changed:
            logger.debug("Script set session vars: %s (script=%s, count=%d)",
                         list(changed.keys()), filename, len(changed))
        return ScriptResult(output_vars=changed)

    @classmethod
    def _collect_changed_env(
        cls,
        new_env: Any,
        session_vars: Dict[str, str],
    ) -> tuple[Dict[str, str], Optional[str]]:
        """Return changed env entries subject to strict output limits."""
        if not isinstance(new_env, dict):
            return {}, "Script env must be a dictionary"

        changed: Dict[str, str] = {}
        total_bytes = 0

        for raw_key, raw_value in new_env.items():
            if not isinstance(raw_key, str):
                return {}, "Script env keys must be strings"
            if len(raw_key) > cls.MAX_ENV_KEY_LENGTH:
                return {}, (
                    f"Script env key too long ({len(raw_key)} chars, "
                    f"max {cls.MAX_ENV_KEY_LENGTH})"
                )

            value_str = str(raw_value)
            if len(value_str) > cls.MAX_ENV_VALUE_LENGTH:
                return {}, (
                    f"Script env value too long for key '{raw_key}' "
                    f"({len(value_str)} chars, max {cls.MAX_ENV_VALUE_LENGTH})"
                )

            if session_vars.get(raw_key) == value_str and raw_key in session_vars:
                continue

            if len(changed) >= cls.MAX_OUTPUT_VARS:
                return {}, (
                    f"Script produced too many env vars "
                    f"(max {cls.MAX_OUTPUT_VARS})"
                )

            entry_bytes = len(raw_key.encode("utf-8")) + len(value_str.encode("utf-8"))
            total_bytes += entry_bytes
            if total_bytes > cls.MAX_OUTPUT_TOTAL_BYTES:
                return {}, (
                    f"Script env output too large ({total_bytes} bytes, "
                    f"max {cls.MAX_OUTPUT_TOTAL_BYTES})"
                )

            changed[raw_key] = value_str

        return changed, None

