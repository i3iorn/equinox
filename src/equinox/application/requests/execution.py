"""Request send orchestration service.

Qt-free. Owns the full preparation pipeline that runs between the GUI
snapshot and worker dispatch:

    1. Collect interpolation variables from DB / session.
    2. Resolve effective auth (own → DB-inherited → cached inherited).
    3. Execute pre-request script (returns result for GUI rendering).
    4. Interpolate all request fields (URL, headers, params, body, path_params).
    5. Validate for unresolved placeholders.
    6. Interpolate auth strategy fields.
    7. Construct the transport-ready ``Request`` object.

The GUI is responsible only for:
- Rendering ``SendReadyPackage.pre_script_result`` labels.
- Updating ``session_vars`` from pre-script env_changes.
- Creating the worker thread with the returned ``Request``.
- Showing blocking issues as dialogs.

This module must never import Qt types.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from equinox.application.requests._assembly import (
    apply_default_headers,
    assemble_body,
    inject_content_type,
)
from equinox.application.requests.models import (
    PreparationIssue,
    SendOrchestratorResult,
    SendReadyPackage,
)
from equinox.application.requests.preparation import (
    collect_unresolved_placeholders,
    interpolate_auth,
    interpolate_request_fields,
)
from equinox.core.interpolation import (
    VariableInterpolator,
    collect_interpolation_variables_detailed,
)
from equinox.core.request import Request
from equinox.core.request.types import AssertionRule, CaptureRule
from equinox.core.scripts import ScriptRunner

if TYPE_CHECKING:
    from equinox.application.requests.models import RequestEditorSnapshot

logger = logging.getLogger(__name__)


# ── Auth resolution ───────────────────────────────────────────────────────────


def _resolve_send_auth(
    own_auth: Any | None,
    inherited_auth: Any | None,
    inherited_auth_source: str | None,
    collection_id: int | None,
    folder: str | None,
    collection_manager: Any | None,
) -> tuple[Any, str | None]:
    """Return ``(effective_auth, source_label)``.

    Resolution order:
    1. ``own_auth`` when set — no inheritance needed.
    2. Live DB resolution via ``collection_manager.resolve_effective_auth``.
    3. Cached ``inherited_auth`` from the GUI as fallback.
    """
    if own_auth is not None:
        return own_auth, None

    if collection_manager is not None and collection_id is not None:
        probe = Request(method="GET", url="", collection_id=collection_id, folder=folder)
        try:
            inh, source = collection_manager.resolve_effective_auth(probe)
            if inh is not None:
                return inh, source
        except Exception as exc:
            logger.debug("Send-time inherited auth resolution failed: %s", exc)

    if inherited_auth is not None:
        return inherited_auth, inherited_auth_source

    return None, None


# ── Pre-script execution ──────────────────────────────────────────────────────


def _run_pre_script(
    pre_script: str,
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    body: str | None,
    variables: dict[str, str],
    session_vars: dict[str, str],
    policy_profile: str,
    ) -> tuple[dict[str, str], Any]:
    """Execute the pre-request script if defined.

    Returns ``(updated_variables, script_result)``.  ``script_result`` is the
    raw ``ScriptResult`` object — the GUI is responsible for rendering it.
    ``updated_variables`` merges any env_changes back into the variable dict.
    When policy is strict or no script is provided returns variables unchanged.
    """
    if str(policy_profile).lower() == "strict":
        logger.debug("Pre-script skipped: strict policy")
        return variables, None

    if not (pre_script or "").strip():
        return variables, None

    req_dict: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": dict(headers),
        "params": dict(params),
        "body": body,
    }
    try:
        result = ScriptRunner.run_pre(pre_script, req_dict, session_vars)
        if not result.error and result.env_changes:
            updated = dict(variables)
            updated.update(result.env_changes)
            return updated, result
        return variables, result
    except Exception as exc:
        logger.debug("Pre-script execution raised an unexpected error: %s", exc, exc_info=True)
        return variables, None


# ── Request object construction ───────────────────────────────────────────────


def _build_request(
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    params_list: list,
    body: str | None,
    effective_auth: Any | None,
    multipart_data: list | None,
    path_params: dict[str, str],
    snapshot: RequestEditorSnapshot,
) -> Request:
    """Construct the transport-ready ``Request`` from interpolated fields."""
    return Request(
        method=method,
        url=url,
        headers=headers,
        params=params,
        params_list=params_list,
        body=body,
        auth=effective_auth,
        timeout=snapshot.timeout,
        verify_ssl=snapshot.verify_ssl,
        follow_redirects=snapshot.follow_redirects,
        name=snapshot.name,
        description=snapshot.description,
        collection_id=snapshot.collection_id,
        folder=snapshot.folder,
        id=snapshot.request_id,
        captures=list(cast(tuple[CaptureRule, ...], snapshot.captures)),
        assertions=list(cast(tuple[AssertionRule, ...], snapshot.assertions)),
        multipart_data=multipart_data,
        pre_script=snapshot.pre_script,
        post_script=snapshot.post_script,
        cert_path=snapshot.cert_path,
        cert_key_path=snapshot.cert_key_path,
        path_params=path_params,
    )


# ── Public service entry point ────────────────────────────────────────────────


def prepare_send(
    snapshot: RequestEditorSnapshot,
    db: Any,
    collection_manager: Any | None,
    own_auth: Any | None,
    inherited_auth: Any | None,
    inherited_auth_source: str | None,
    policy_profile: str,
) -> SendOrchestratorResult:
    """Prepare a snapshot for HTTP dispatch.

    This is the single send service entry point.  It is Qt-free and accepts
    only plain Python types so it can be unit-tested without a display server.

    Args:
        snapshot: Editor state snapshot built by the GUI.
        db: ``Database`` instance for variable resolution.
        collection_manager: ``CollectionManager`` for auth hierarchy resolution.
            May be ``None`` when there is no active collection context.
        own_auth: The request's own ``AuthStrategy``, or ``None``.
        inherited_auth: Cached inherited auth from the GUI, or ``None``.
        inherited_auth_source: Source label for ``inherited_auth``, or ``None``.
        policy_profile: Active security policy (``"balanced"`` / ``"strict"``).

    Returns:
        ``SendOrchestratorResult`` — either ``ready`` with a ``SendReadyPackage``
        or non-empty ``blocking_issues`` that the GUI must present and abort on.
    """
    url = snapshot.url

    # ── 1. Collect interpolation variables ──────────────────────────────────
    try:
        variables, variable_sources = collect_interpolation_variables_detailed(
            db,
            collection_id=snapshot.collection_id,
            session_vars=snapshot.session_vars,
        )
    except Exception as exc:
        logger.error("Variable collection failed: %s", exc, exc_info=True)
        return SendOrchestratorResult(
            blocking_issues=(
                PreparationIssue(
                    code="variables.collection_failed",
                    message=f"Failed to collect variables: {exc}",
                    severity="error",
                ),
            )
        )

    # ── 2. Resolve effective auth ────────────────────────────────────────────
    effective_auth, resolved_source = _resolve_send_auth(
        own_auth=own_auth,
        inherited_auth=inherited_auth,
        inherited_auth_source=inherited_auth_source,
        collection_id=snapshot.collection_id,
        folder=snapshot.folder,
        collection_manager=collection_manager,
    )
    is_auth_inherited = own_auth is None and effective_auth is not None

    # ── 3. Assemble body ─────────────────────────────────────────────────────
    try:
        method = snapshot.method
        headers = dict(snapshot.headers)
        params = dict(snapshot.params)
        params_list = list(snapshot.params_list)
        body, multipart_data = assemble_body(
            snapshot.body_type,
            (snapshot.body or "").strip(),
            snapshot.graphql_query.strip(),
            snapshot.graphql_variables.strip(),
            list(snapshot.multipart_data),
        )
        path_params = dict(snapshot.path_params)
        headers = inject_content_type(body, snapshot.body_type, headers)
    except ValueError as exc:
        return SendOrchestratorResult(
            blocking_issues=(
                PreparationIssue(
                    code="body.assembly_failed",
                    message=str(exc),
                    severity="error",
                    field_name="body",
                ),
            )
        )

    # ── 4. Run pre-script ────────────────────────────────────────────────────
    variables, pre_script_result = _run_pre_script(
        pre_script=snapshot.pre_script,
        method=method,
        url=url,
        headers=headers,
        params=params,
        body=body,
        variables=variables,
        session_vars=dict(snapshot.session_vars),
        policy_profile=policy_profile,
    )

    # ── 5. Interpolate all request fields ────────────────────────────────────
    try:
        url, headers, params, body, path_params = interpolate_request_fields(
            url, headers, params, body, path_params, variables
        )
    except Exception as exc:
        logger.warning("Variable interpolation failed: %s", exc)
        return SendOrchestratorResult(
            blocking_issues=(
                PreparationIssue(
                    code="interpolation.failed",
                    message=f"Failed to expand variables: {exc}",
                    severity="error",
                ),
            )
        )

    if path_params:
        from equinox.core.urls import expand_placeholders

        url = expand_placeholders(url, path_params)
        logger.debug("URL expanded with path_params: %s", url[:100])

    # ── 6. Check for unresolved placeholders ─────────────────────────────────
    unresolved = collect_unresolved_placeholders(url, headers, params, body, path_params)
    if unresolved:
        unresolved_details = []
        for name in unresolved:
            value = variables.get(name)
            unresolved_details.append(
                f"{name}(source={variable_sources.get(name, 'missing')}, "
                f"value_type={type(value).__name__ if value is not None else 'missing'}, "
                f"value_is_template={bool(isinstance(value, str) and VariableInterpolator.has_variables(value))})"
            )
        logger.warning(
            "Unresolved placeholders before dispatch: %s (available_keys=%s)",
            unresolved_details,
            sorted(str(k) for k in variables.keys()),
        )
        return SendOrchestratorResult(
            blocking_issues=(
                PreparationIssue(
                    code="variables.unresolved",
                    message=(
                        "Failed to expand variables:\n"
                        f"Unresolved placeholders: {', '.join(unresolved_details)}"
                    ),
                    severity="error",
                    field_name="url",
                ),
            )
        )

    # ── 7. Interpolate auth fields ───────────────────────────────────────────
    try:
        effective_auth = interpolate_auth(
            effective_auth,
            lambda s: VariableInterpolator.interpolate(s, variables),
        )
    except Exception as exc:
        logger.warning("Auth variable interpolation failed: %s", exc)
        return SendOrchestratorResult(
            blocking_issues=(
                PreparationIssue(
                    code="auth.interpolation_failed",
                    message=f"Failed to expand variables in auth fields: {exc}",
                    severity="error",
                    field_name="auth",
                ),
            )
        )

    # ── 8. Construct transport-ready Request ─────────────────────────────────
    try:
        request = _build_request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            params_list=params_list,
            body=body,
            effective_auth=effective_auth,
            multipart_data=multipart_data,
            path_params=path_params,
            snapshot=snapshot,
        )
        apply_default_headers(request)
    except Exception as exc:
        logger.error("Request construction failed: %s", exc, exc_info=True)
        return SendOrchestratorResult(
            blocking_issues=(
                PreparationIssue(
                    code="request.construction_failed",
                    message=f"Failed to build request: {exc}",
                    severity="error",
                ),
            )
        )

    return SendOrchestratorResult(
        package=SendReadyPackage(
            request=request,
            variables=variables,
            variable_sources=variable_sources,
            pre_script_result=pre_script_result,
            inherited_auth_source=resolved_source,
            is_auth_inherited=is_auth_inherited,
        )
    )
