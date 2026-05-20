from __future__ import annotations

from dataclasses import dataclass

from equinox.application.requests import (
    build_preflight_issues,
    collect_unresolved_placeholders,
    interpolate_auth,
    interpolate_request_fields,
    issues_to_messages,
    resolve_path_params,
)


@dataclass
class _DummyAuth:
    value: str

    def get_preflight_warning(self) -> str:
        return f"auth-warning:{self.value}"

    def interpolate(self, interp):
        return _DummyAuth(interp(self.value))


def test_resolve_path_params_supports_chained_values() -> None:
    resolved = resolve_path_params(
        {"entity": "{{USER_ID}}", "resource": "{{entity}}"},
        {"USER_ID": "42"},
    )

    assert resolved == {"entity": "42", "resource": "42"}


def test_interpolate_request_fields_uses_resolved_path_params() -> None:
    result = interpolate_request_fields(
        url="https://api.example.com/{{resource}}",
        headers={"X-Entity": "{{entity}}"},
        params={"id": "{{resource}}", "owner": "{{USER_ID}}"},
        body='{"ref":"{{resource}}"}',
        path_params={"entity": "{{USER_ID}}", "resource": "{{entity}}"},
        variables={"USER_ID": "42"},
    )

    assert result == (
        "https://api.example.com/42",
        {"X-Entity": "42"},
        {"id": "42", "owner": "42"},
        '{"ref":"42"}',
        {"entity": "42", "resource": "42"},
    )


def test_collect_unresolved_placeholders_scans_all_fields() -> None:
    unresolved = collect_unresolved_placeholders(
        url="https://{{BASE_URL}}/v1/{{resource}}",
        headers={"X-{{HKEY}}": "{{HVAL}}"},
        params={"{{PKEY}}": "{{PVAL}}"},
        body='{"id":"{{BODY_ID}}"}',
        path_params={"k": "{{PATH_ID}}"},
    )

    assert unresolved == [
        "BASE_URL",
        "BODY_ID",
        "HKEY",
        "HVAL",
        "PATH_ID",
        "PKEY",
        "PVAL",
        "resource",
    ]


def test_build_preflight_issues_returns_structured_warnings() -> None:
    issues = build_preflight_issues(
        url="http://example.com",
        policy_profile="Strict",
        verify_ssl=False,
        follow_redirects=True,
        pre_script="print('pre')",
        post_script="print('post')",
        auth=_DummyAuth("x"),
    )

    assert [issue.code for issue in issues] == [
        "policy.insecure_http",
        "policy.ssl_required",
        "policy.redirects",
        "policy.scripts_disabled",
        "auth.preflight_warning",
    ]
    assert issues_to_messages(issues) == [
        "Strict policy blocks insecure HTTP requests; use https://",
        "Strict policy requires SSL verification",
        "Strict policy recommends disabling redirects",
        "Strict policy disables pre/post scripts",
        "auth-warning:x",
    ]


def test_interpolate_auth_returns_changed_auth_when_supported() -> None:
    auth = _DummyAuth("{{TOKEN}}")
    resolved = interpolate_auth(auth, lambda value: value.replace("{{TOKEN}}", "abc"))

    assert isinstance(resolved, _DummyAuth)
    assert resolved.value == "abc"


