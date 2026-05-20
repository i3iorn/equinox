"""Plain data contracts for request application services.

Phase 2 keeps these models free of Qt dependencies so later request-service
logic can be unit-tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]


@dataclass(slots=True)
class RequestEditorSnapshot:
    """Immutable-by-convention snapshot of editor state.

    The GUI will populate this with plain Python values only. The request
    service layer can then prepare, validate, and execute requests without
    reaching back into Qt widgets.
    """

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    params_list: tuple[JsonDict, ...] = ()
    body: str | None = None
    body_type: str = "none"
    graphql_query: str = ""
    graphql_variables: str = ""
    multipart_data: tuple[JsonDict, ...] = ()
    path_params: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    verify_ssl: bool = True
    follow_redirects: bool = True
    name: str | None = None
    description: str | None = None
    collection_id: int | None = None
    folder: str | None = None
    request_id: int | None = None
    auth_type: str | None = None
    auth_data: dict[str, Any] = field(default_factory=dict)
    inherited_auth_type: str | None = None
    inherited_auth_data: dict[str, Any] = field(default_factory=dict)
    inherited_auth_source: str | None = None
    captures: tuple[JsonDict, ...] = ()
    assertions: tuple[JsonDict, ...] = ()
    pre_script: str = ""
    post_script: str = ""
    cert_path: str | None = None
    cert_key_path: str | None = None
    session_vars: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class PreparationIssue:
    """Describe a validation or preparation issue."""

    code: str
    message: str
    severity: str = "warning"
    field_name: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreparedRequest:
    """Prepared request data ready for construction or dispatch."""

    snapshot: RequestEditorSnapshot
    request_kwargs: dict[str, Any]
    normalized_url: str
    resolved_headers: dict[str, str] = field(default_factory=dict)
    resolved_params: dict[str, str] = field(default_factory=dict)
    resolved_body: str | None = None
    resolved_path_params: dict[str, str] = field(default_factory=dict)
    issues: tuple[PreparationIssue, ...] = ()


@dataclass(slots=True)
class PreparationResult:
    """Outcome of request preparation."""

    snapshot: RequestEditorSnapshot
    prepared_request: PreparedRequest | None = None
    issues: tuple[PreparationIssue, ...] = ()
    ready_to_send: bool = False


@dataclass(slots=True)
class ExecutionResult:
    """Outcome of a request execution attempt."""

    prepared_request: PreparedRequest
    response: Any | None = None
    error: Exception | None = None
    cancelled: bool = False
    elapsed_ms: float | None = None
    issues: tuple[PreparationIssue, ...] = ()


@dataclass(slots=True)
class SendReadyPackage:
    """Fully assembled, interpolated request ready for worker dispatch.

    The service layer produces this after completing all preparation steps.
    The GUI consumes it to create a worker thread — no further assembly needed.
    """

    request: Any  # equinox.core.request.Request (typed as Any to avoid circular import)
    variables: dict[str, Any]
    variable_sources: dict[str, str]
    pre_script_result: Any | None  # ScriptResult or None — GUI renders this
    inherited_auth_source: str | None
    is_auth_inherited: bool


@dataclass(slots=True)
class SendOrchestratorResult:
    """Outcome of send orchestration — either a ready package or blocking issues.

    When ``package`` is set the GUI may proceed to worker dispatch.
    When ``blocking_issues`` is non-empty the GUI must present them and abort.
    """

    package: SendReadyPackage | None = None
    blocking_issues: tuple[PreparationIssue, ...] = ()

    @property
    def ready(self) -> bool:
        """True when the request is fully prepared and there are no blockers."""
        return self.package is not None and not self.blocking_issues
