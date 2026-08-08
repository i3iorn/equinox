"""Request post-processing service helpers.

Qt-free helpers that keep post-send business decisions out of GUI widgets.
The GUI remains responsible for rendering labels/panels and executing side
effects using the returned plain-data outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from equinox.auth import OAuth2Auth
from equinox.core.captures import CaptureEngine
from equinox.core.scripts import ScriptRunner


@dataclass(slots=True)
class CaptureProcessingOutcome:
    """Outcome of applying capture rules to a response."""

    session_updates: dict[str, str] = field(default_factory=dict)
    display_lines: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class PostScriptOutcome:
    """Outcome of post-script execution decisions."""

    skipped: bool = False
    skip_message: str | None = None
    script_result: Any | None = None
    error: str | None = None


@dataclass(slots=True)
class DeferredPersistencePlan:
    """Deferred persistence actions decided by the service layer."""

    save_history: bool
    history_error: str | None = None
    persist_inherited_token: bool = False
    persist_own_oauth2_token: bool = False


@dataclass(slots=True)
class ErrorHandlingPlan:
    """Plain-data plan for GUI error handling."""

    status_message: str
    dialog_title: str
    dialog_text: str
    copy_text: str
    log_panel_message: str
    deferred_plan: DeferredPersistencePlan


@dataclass(slots=True)
class SuccessHandlingPlan:
    """Plain-data plan for GUI success handling."""

    elapsed_ms: int
    status_message: str
    completer_url: str
    deferred_plan: DeferredPersistencePlan


def apply_captures(response: Any) -> CaptureProcessingOutcome:
    """Apply captures defined on ``response.request`` and return plain results."""
    try:
        caps_raw = getattr(response.request, "captures", [])
        if not caps_raw:
            return CaptureProcessingOutcome()

        results = CaptureEngine.apply_all(CaptureEngine.from_dict_list(caps_raw), response)
        updates: dict[str, str] = {}
        lines: list[str] = []
        for item in results:
            updates[item.variable] = item.value
            marker = "✓" if item.success else "✗"
            suffix = f"  ({item.error})" if not item.success else ""
            lines.append(f"{marker} {item.variable} = {item.value!r}{suffix}")
        return CaptureProcessingOutcome(session_updates=updates, display_lines=lines)
    except Exception as exc:
        return CaptureProcessingOutcome(error=str(exc) or type(exc).__name__)


def run_post_script(
    *,
    policy_profile: str,
    post_script: str,
    response: Any,
    session_vars: dict[str, str],
) -> PostScriptOutcome:
    """Execute post-script policy/dispatch decisions and return the outcome."""
    if str(policy_profile).lower() == "strict":
        return PostScriptOutcome(skipped=True, skip_message="Skipped by strict policy")

    if not (post_script or "").strip():
        return PostScriptOutcome()

    try:
        resp_dict: dict[str, Any] = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": response.text if hasattr(response, "text") else "",
            "json": None,
        }
        try:
            resp_dict["json"] = response.json()
        except Exception:
            pass

        result = ScriptRunner.run_post(post_script, resp_dict, session_vars)
        return PostScriptOutcome(script_result=result)
    except Exception as exc:
        return PostScriptOutcome(error=str(exc) or type(exc).__name__)


def build_deferred_persistence_plan(
    *,
    request: Any,
    error_message: str | None,
    send_inherited_auth: Any,
    send_inherited_source: str | None,
    own_auth: Any,
) -> DeferredPersistencePlan:
    """Return deferred persistence decisions for success or error flows."""
    persist_inherited = bool(
        isinstance(send_inherited_auth, OAuth2Auth)
        and bool(getattr(send_inherited_auth, "access_token", ""))
        and bool(send_inherited_source)
        and bool(getattr(request, "collection_id", None)),
    )
    persist_own = bool(
        isinstance(own_auth, OAuth2Auth)
        and bool(getattr(own_auth, "access_token", ""))
        and bool(getattr(request, "id", None)),
    )
    return DeferredPersistencePlan(
        save_history=True,
        history_error=error_message,
        persist_inherited_token=persist_inherited,
        persist_own_oauth2_token=persist_own,
    )


def build_error_handling_plan(
    *,
    error: Any,
    request: Any,
    log_file_path: str | None,
    send_inherited_auth: Any,
    send_inherited_source: str | None,
    own_auth: Any,
) -> ErrorHandlingPlan:
    """Build the GUI-facing error handling plan from plain inputs."""
    message = str(getattr(error, "message", "") or "Unknown error")
    exc_type = str(getattr(error, "exc_type", type(error).__name__))
    hint = getattr(error, "hint", None)
    copy_text = str(getattr(error, "tb", "") or "")

    log_hint = f"\n\nFull details in: {log_file_path}" if log_file_path else ""
    dialog_text = f"{message}{log_hint}"
    if hint:
        dialog_text = f"{message}\n\n{hint}{log_hint}"

    deferred = build_deferred_persistence_plan(
        request=request,
        error_message=message,
        send_inherited_auth=send_inherited_auth,
        send_inherited_source=send_inherited_source,
        own_auth=own_auth,
    )
    return ErrorHandlingPlan(
        status_message=f"Error: {message}",
        dialog_title=f"Request Failed — {exc_type}",
        dialog_text=dialog_text,
        copy_text=copy_text,
        log_panel_message=message,
        deferred_plan=deferred,
    )


def build_success_handling_plan(
    *,
    response: Any,
    send_inherited_auth: Any,
    send_inherited_source: str | None,
    own_auth: Any,
) -> SuccessHandlingPlan:
    """Build the GUI-facing success handling plan from plain inputs."""
    elapsed_ms = int(float(getattr(response, "elapsed", 0.0)) * 1000)
    request = getattr(response, "request", None)
    deferred = build_deferred_persistence_plan(
        request=request,
        error_message=None,
        send_inherited_auth=send_inherited_auth,
        send_inherited_source=send_inherited_source,
        own_auth=own_auth,
    )
    return SuccessHandlingPlan(
        elapsed_ms=elapsed_ms,
        status_message=f"{response.status_code} {response.reason}  —  {elapsed_ms} ms",
        completer_url=getattr(request, "url", "") or "",
        deferred_plan=deferred,
    )
