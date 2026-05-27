"""Request application services and data contracts.

Phase 2 introduces the request-service seam here without importing Qt.
Phase 5 adds the send orchestration service (``prepare_send``).
"""

from ._assembly import detect_body_type
from .execution import prepare_send
from .history import RequestHistoryService
from .models import (
    ExecutionResult,
    PreparationIssue,
    PreparationResult,
    PreparedRequest,
    RequestEditorSnapshot,
    SendOrchestratorResult,
    SendReadyPackage,
)
from .persistence import RequestPersistenceFacade, SaveRequestResult
from .post_processing import (
    CaptureProcessingOutcome,
    DeferredPersistencePlan,
    ErrorHandlingPlan,
    PostScriptOutcome,
    SuccessHandlingPlan,
    apply_captures,
    build_deferred_persistence_plan,
    build_error_handling_plan,
    build_success_handling_plan,
    run_post_script,
)
from .preparation import (
    build_preflight_issues,
    collect_unresolved_placeholders,
    interpolate_auth,
    interpolate_request_fields,
    issues_to_messages,
    resolve_path_params,
)

__all__ = [
    # models
    "ExecutionResult",
    "PreparationIssue",
    "PreparationResult",
    "PreparedRequest",
    "RequestEditorSnapshot",
    "SendOrchestratorResult",
    "SendReadyPackage",
    # preparation helpers
    "build_preflight_issues",
    "collect_unresolved_placeholders",
    "interpolate_auth",
    "interpolate_request_fields",
    "issues_to_messages",
    "resolve_path_params",
    # send orchestration
    "prepare_send",
    # history boundary
    "RequestHistoryService",
    # persistence boundary
    "RequestPersistenceFacade",
    "SaveRequestResult",
    # post-send orchestration
    "CaptureProcessingOutcome",
    "DeferredPersistencePlan",
    "ErrorHandlingPlan",
    "PostScriptOutcome",
    "SuccessHandlingPlan",
    "apply_captures",
    "build_deferred_persistence_plan",
    "build_error_handling_plan",
    "build_success_handling_plan",
    "detect_body_type",
    "run_post_script",
]
