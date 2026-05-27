"""Request application services and data contracts.

Phase 2 introduces the request-service seam here without importing Qt.
Phase 5 adds the send orchestration service (``prepare_send``).
"""
from ._assembly import detect_body_type
from .execution import prepare_send
from .history import RequestHistoryService
from .models import ExecutionResult
from .models import PreparationIssue
from .models import PreparationResult
from .models import PreparedRequest
from .models import RequestEditorSnapshot
from .models import SendOrchestratorResult
from .models import SendReadyPackage
from .persistence import RequestPersistenceFacade
from .persistence import SaveRequestResult
from .post_processing import apply_captures
from .post_processing import build_deferred_persistence_plan
from .post_processing import build_error_handling_plan
from .post_processing import build_success_handling_plan
from .post_processing import CaptureProcessingOutcome
from .post_processing import DeferredPersistencePlan
from .post_processing import ErrorHandlingPlan
from .post_processing import PostScriptOutcome
from .post_processing import run_post_script
from .post_processing import SuccessHandlingPlan
from .preparation import build_preflight_issues
from .preparation import collect_unresolved_placeholders
from .preparation import interpolate_auth
from .preparation import interpolate_request_fields
from .preparation import issues_to_messages
from .preparation import resolve_path_params

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
