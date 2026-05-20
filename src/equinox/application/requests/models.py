"""Plain data contracts for request application services.

Phase 2 keeps these models free of Qt dependencies so later request-service
logic can be unit-tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

JsonDict = Dict[str, Any]


@dataclass(slots=True)
class RequestEditorSnapshot:
    """Immutable-by-convention snapshot of editor state.

    The GUI will populate this with plain Python values only. The request
    service layer can then prepare, validate, and execute requests without
    reaching back into Qt widgets.
    """

    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, str] = field(default_factory=dict)
    params_list: Tuple[JsonDict, ...] = ()
    body: Optional[str] = None
    body_type: str = "none"
    graphql_query: str = ""
    graphql_variables: str = ""
    multipart_data: Tuple[JsonDict, ...] = ()
    path_params: Dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    verify_ssl: bool = True
    follow_redirects: bool = True
    name: Optional[str] = None
    description: Optional[str] = None
    collection_id: Optional[int] = None
    folder: Optional[str] = None
    request_id: Optional[int] = None
    auth_type: Optional[str] = None
    auth_data: Dict[str, Any] = field(default_factory=dict)
    inherited_auth_type: Optional[str] = None
    inherited_auth_data: Dict[str, Any] = field(default_factory=dict)
    inherited_auth_source: Optional[str] = None
    captures: Tuple[JsonDict, ...] = ()
    assertions: Tuple[JsonDict, ...] = ()
    pre_script: str = ""
    post_script: str = ""
    cert_path: Optional[str] = None
    cert_key_path: Optional[str] = None
    session_vars: Dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class PreparationIssue:
    """Describe a validation or preparation issue."""

    code: str
    message: str
    severity: str = "warning"
    field_name: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreparedRequest:
    """Prepared request data ready for construction or dispatch."""

    snapshot: RequestEditorSnapshot
    request_kwargs: Dict[str, Any]
    normalized_url: str
    resolved_headers: Dict[str, str] = field(default_factory=dict)
    resolved_params: Dict[str, str] = field(default_factory=dict)
    resolved_body: Optional[str] = None
    resolved_path_params: Dict[str, str] = field(default_factory=dict)
    issues: Tuple[PreparationIssue, ...] = ()


@dataclass(slots=True)
class PreparationResult:
    """Outcome of request preparation."""

    snapshot: RequestEditorSnapshot
    prepared_request: Optional[PreparedRequest] = None
    issues: Tuple[PreparationIssue, ...] = ()
    ready_to_send: bool = False


@dataclass(slots=True)
class ExecutionResult:
    """Outcome of a request execution attempt."""

    prepared_request: PreparedRequest
    response: Optional[Any] = None
    error: Optional[Exception] = None
    cancelled: bool = False
    elapsed_ms: Optional[float] = None
    issues: Tuple[PreparationIssue, ...] = ()


@dataclass(slots=True)
class SendReadyPackage:
    """Fully assembled, interpolated request ready for worker dispatch.

    The service layer produces this after completing all preparation steps.
    The GUI consumes it to create a worker thread — no further assembly needed.
    """

    request: Any  # equinox.core.request.Request (typed as Any to avoid circular import)
    variables: Dict[str, Any]
    variable_sources: Dict[str, str]
    pre_script_result: Optional[Any]  # ScriptResult or None — GUI renders this
    inherited_auth_source: Optional[str]
    is_auth_inherited: bool


@dataclass(slots=True)
class SendOrchestratorResult:
    """Outcome of send orchestration — either a ready package or blocking issues.

    When ``package`` is set the GUI may proceed to worker dispatch.
    When ``blocking_issues`` is non-empty the GUI must present them and abort.
    """

    package: Optional[SendReadyPackage] = None
    blocking_issues: Tuple[PreparationIssue, ...] = ()

    @property
    def ready(self) -> bool:
        """True when the request is fully prepared and there are no blockers."""
        return self.package is not None and not self.blocking_issues


