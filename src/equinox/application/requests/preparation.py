"""Qt-free request preparation helpers for the application boundary.

This module holds the pure rules that prepare editor state for request send
and save flows. It intentionally avoids Qt imports so the helpers can be unit
tested in isolation.
"""

from __future__ import annotations

import logging
import re
from typing import Callable

from equinox.application.requests.models import PreparationIssue
from equinox.auth import AuthStrategy
from equinox.core.interpolation import VariableInterpolator

logger = logging.getLogger(__name__)

_UNRESOLVED_VAR_RE = re.compile(r"\{\{([a-zA-Z0-9_-]+)}}")


def build_preflight_issues(
    *,
    url: str,
    policy_profile: str,
    verify_ssl: bool,
    follow_redirects: bool,
    pre_script: str,
    post_script: str,
    auth: AuthStrategy | None,
) -> tuple[PreparationIssue, ...]:
    """Return structured preflight issues for the current editor state."""
    issues: list[PreparationIssue] = []
    normalized_profile = str(policy_profile or "balanced").lower()
    cleaned_url = (url or "").strip()

    if (
        cleaned_url
        and "{{" not in cleaned_url
        and not cleaned_url.lower().startswith(("http://", "https://"))
    ):
        issues.append(
            PreparationIssue(
                code="url.scheme",
                message="URL does not start with http:// or https://",
                severity="warning",
                field_name="url",
            )
        )

    if normalized_profile == "strict":
        if cleaned_url.lower().startswith("http://"):
            issues.append(
                PreparationIssue(
                    code="policy.insecure_http",
                    message="Strict policy blocks insecure HTTP requests; use https://",
                    severity="warning",
                    field_name="url",
                )
            )
        if not verify_ssl:
            issues.append(
                PreparationIssue(
                    code="policy.ssl_required",
                    message="Strict policy requires SSL verification",
                    severity="warning",
                    field_name="verify_ssl",
                )
            )
        if follow_redirects:
            issues.append(
                PreparationIssue(
                    code="policy.redirects",
                    message="Strict policy recommends disabling redirects",
                    severity="warning",
                    field_name="follow_redirects",
                )
            )
        if (pre_script or "").strip() or (post_script or "").strip():
            issues.append(
                PreparationIssue(
                    code="policy.scripts_disabled",
                    message="Strict policy disables pre/post scripts",
                    severity="warning",
                    field_name="scripts",
                )
            )

    if auth is not None and hasattr(auth, "get_preflight_warning"):
        warning = auth.get_preflight_warning()
        if warning:
            issues.append(
                PreparationIssue(
                    code="auth.preflight_warning",
                    message=str(warning),
                    severity="warning",
                    field_name="auth",
                )
            )

    return tuple(issues)


def issues_to_messages(issues: tuple[PreparationIssue, ...]) -> list[str]:
    """Convert preparation issues into user-facing warning strings."""
    messages: list[str] = []
    for issue in issues:
        if issue.field_name:
            messages.append(f"{issue.message}")
        else:
            messages.append(issue.message)
    return messages


def resolve_path_params(path_params: dict[str, str], variables: dict[str, str]) -> dict[str, str]:
    """Resolve path params against global vars and other path params."""
    resolved: dict[str, str] = {}
    for key, value in path_params.items():
        resolved_key = VariableInterpolator.interpolate(key, variables)
        resolved[resolved_key] = value

    resolved_values: dict[str, str] = {}
    for key, value in resolved.items():
        context = dict(variables)
        context.update(resolved)
        if key in variables:
            context[key] = variables[key]
        resolved_values[key] = VariableInterpolator.interpolate(value, context)
    return resolved_values


def interpolate_request_fields(
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    body: str | None,
    path_params: dict[str, str],
    variables: dict[str, str],
) -> tuple[str, dict[str, str], dict[str, str], str | None, dict[str, str]]:
    """Interpolate ``{{VAR}}`` placeholders in all request fields."""
    logger.debug("Interpolating variables in request (url_len=%d)", len(url))
    resolved_path_params = resolve_path_params(path_params, variables)
    merged_vars = dict(variables)
    merged_vars.update(resolved_path_params)

    resolved_url = VariableInterpolator.interpolate(url, merged_vars)
    resolved_headers = {
        VariableInterpolator.interpolate(key, merged_vars): VariableInterpolator.interpolate(
            value, merged_vars
        )
        for key, value in headers.items()
    }
    resolved_params = {
        VariableInterpolator.interpolate(key, merged_vars): VariableInterpolator.interpolate(
            value, merged_vars
        )
        for key, value in params.items()
    }
    resolved_body = VariableInterpolator.interpolate(body, merged_vars) if body else None
    logger.debug("Variable interpolation completed successfully")
    return resolved_url, resolved_headers, resolved_params, resolved_body, resolved_path_params


def collect_unresolved_placeholders(
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    body: str | None,
    path_params: dict[str, str],
) -> list[str]:
    """Return unresolved placeholder names across all request fields."""
    unresolved = set(_UNRESOLVED_VAR_RE.findall(url or ""))
    for key, value in headers.items():
        unresolved.update(_UNRESOLVED_VAR_RE.findall(key or ""))
        unresolved.update(_UNRESOLVED_VAR_RE.findall(value or ""))
    for key, value in params.items():
        unresolved.update(_UNRESOLVED_VAR_RE.findall(key or ""))
        unresolved.update(_UNRESOLVED_VAR_RE.findall(value or ""))
    if body:
        unresolved.update(_UNRESOLVED_VAR_RE.findall(body))
    for key, value in path_params.items():
        unresolved.update(_UNRESOLVED_VAR_RE.findall(key or ""))
        unresolved.update(_UNRESOLVED_VAR_RE.findall(value or ""))
    return sorted(unresolved)


def interpolate_auth(
    auth: AuthStrategy | None,
    interp: Callable[[str], str],
) -> AuthStrategy | None:
    """Interpolate ``{{VAR}}`` placeholders in auth strategy fields."""
    if auth is None:
        return None

    try:
        return auth.interpolate(interp)
    except Exception:
        logger.debug(
            "preparation.interpolate_auth: %s.interpolate() failed — returning unchanged",
            type(auth).__name__,
            exc_info=True,
        )
        return auth
