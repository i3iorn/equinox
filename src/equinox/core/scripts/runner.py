from __future__ import annotations
import logging
import multiprocessing
import queue
import time
from typing import Any, Dict, Optional

from .models import ScriptResult
from .sandbox import get_safe_builtins
from .validation import _validate_ast

logger = logging.getLogger(__name__)

def _subprocess_exec_target(
    q: "multiprocessing.Queue[Any]",
    source: str,
    extra_locals: Dict[str, Any],
    session_vars: Dict[str, str],
    filename: str,
) -> None:
    """Top-level subprocess target for sandboxed script execution."""
    try:
        # get_safe_builtins is reconstructed here inside the child process.
        globs: Dict[str, Any] = {"__builtins__": get_safe_builtins()}
        locs: Dict[str, Any] = {"env": dict(session_vars)}
        locs.update(extra_locals)
        exec(compile(source, filename, "exec"), globs, locs)  # noqa: S102
        q.put(("ok", locs.get("env", {})))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", str(exc)))

class ScriptRunner:
    """Execute sandboxed Python scripts."""

    MAX_SOURCE_LENGTH = 64 * 1024
    EXECUTION_TIMEOUT = 10.0
    MAX_OUTPUT_VARS = 128
    MAX_OUTPUT_TOTAL_BYTES = 16 * 1024
    MAX_ENV_KEY_LENGTH = 128
    MAX_ENV_VALUE_LENGTH = 4096

    @classmethod
    def run_pre(
        cls,
        script: str,
        request_dict: Dict[str, Any],
        session_vars: Dict[str, str],
    ) -> ScriptResult:
        logger.debug("Running pre-request script", extra={
            "script_length": len(script),
            "session_var_count": len(session_vars),
        })
        result = cls._run(script, {"request": dict(request_dict)}, session_vars, "<pre_script>")
        if result.error:
            logger.warning("Pre-request script failed: %s", result.error)
        return result

    @classmethod
    def run_post(
        cls,
        script: str,
        response_dict: Dict[str, Any],
        session_vars: Dict[str, str],
    ) -> ScriptResult:
        logger.debug("Running post-response script", extra={
            "script_length": len(script),
            "session_var_count": len(session_vars),
        })
        result = cls._run(script, {"response": dict(response_dict)}, session_vars, "<post_script>")
        if result.error:
            logger.warning("Post-response script failed: %s", result.error)
        return result

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
            return ScriptResult(error=f"Script too long ({len(script)} chars, max {cls.MAX_SOURCE_LENGTH})")

        try:
            tree = _validate_ast(script, filename)
            compile(tree, filename, "exec")
        except Exception as exc:  # noqa: BLE001
            return ScriptResult(error=str(exc))

        start_time = time.time()

        try:
            q = multiprocessing.Queue()
            p = multiprocessing.Process(
                target=_subprocess_exec_target,
                args=(q, script, extra_locals, session_vars, filename),
                daemon=True,
            )
            p.start()
        except Exception as e:
            string = f"Failed to start script execution: {e}"
            return ScriptResult(error=string, duration=time.time() - start_time)

        try:
            status, data = q.get(timeout=cls.EXECUTION_TIMEOUT)
            if status == "error":
                return ScriptResult(error=data, duration=time.time() - start_time)
            
            output_vars = cls._collect_changed_env(data, session_vars)
            return ScriptResult(env_changes=output_vars, duration=time.time() - start_time)
        except queue.Empty:
            p.terminate()
            return ScriptResult(error=f"Script timed out after {cls.EXECUTION_TIMEOUT}s", duration=time.time() - start_time)
        except ValueError as ve:
            return ScriptResult(error=str(ve), duration=time.time() - start_time)
        finally:
            if p.is_alive():
                p.terminate()

    @classmethod
    def _collect_changed_env(cls, new_env: Any, session_vars: Dict[str, str]) -> Dict[str, str]:
        if not isinstance(new_env, dict):
            return {}
        
        changed: Dict[str, str] = {}
        total_bytes = 0
        
        for k, v in new_env.items():
            if isinstance(k, int):
                raise ValueError("Environment keys must be strings")
            
            sk, sv = str(k), str(v)
            if k not in session_vars or session_vars[k] != sv:
                if len(changed) >= cls.MAX_OUTPUT_VARS:
                    raise ValueError(f"Too many environment variables (max {cls.MAX_OUTPUT_VARS})")
                
                if len(sk) > cls.MAX_ENV_KEY_LENGTH:
                    raise ValueError(f"Environment key too long (max {cls.MAX_ENV_KEY_LENGTH})")
                if len(sv) > cls.MAX_ENV_VALUE_LENGTH:
                    raise ValueError(f"Environment value too long (max {cls.MAX_ENV_VALUE_LENGTH})")
                
                entry_size = len(sk.encode("utf-8")) + len(sv.encode("utf-8"))
                if total_bytes + entry_size > cls.MAX_OUTPUT_TOTAL_BYTES:
                    raise ValueError(f"Total environment size too large (max {cls.MAX_OUTPUT_TOTAL_BYTES} bytes)")
                
                changed[sk] = sv
                total_bytes += entry_size
        return changed
