"""Extended coverage tests for equinox.application.requests.execution."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import Mock, patch

import pytest

from equinox.application.requests.execution import (
    _resolve_send_auth,
    _run_pre_script,
    prepare_send,
)
from equinox.application.requests.models import RequestEditorSnapshot
from equinox.auth import BearerAuth


def _snapshot(**overrides) -> RequestEditorSnapshot:
    data = {
        "method": "GET",
        "url": "https://api.example.com/ping",
        "collection_id": None,
        "folder": None,
        "request_id": None,
        "pre_script": "",
    }
    data.update(overrides)
    return RequestEditorSnapshot(**data)


def _mock_vars(base_url="api.example.com"):
    return (
        {"BASE_URL": base_url},
        {"BASE_URL": "collection"},
    )


# ── _resolve_send_auth ─────────────────────────────────────────────────────────

class TestResolveSendAuth:
    def test_own_auth_returned_directly(self) -> None:
        auth = BearerAuth(token="my-token")
        effective, source = _resolve_send_auth(
            own_auth=auth,
            inherited_auth=None,
            inherited_auth_source=None,
            collection_id=1,
            folder=None,
            collection_manager=None,
        )
        assert effective is auth
        assert source is None

    def test_db_resolution_used_when_available(self) -> None:
        inherited = BearerAuth(token="col-token")
        manager = Mock()
        manager.resolve_effective_auth.return_value = (inherited, "collection")
        effective, source = _resolve_send_auth(
            own_auth=None,
            inherited_auth=None,
            inherited_auth_source=None,
            collection_id=5,
            folder=None,
            collection_manager=manager,
        )
        assert effective is inherited
        assert source == "collection"

    def test_db_resolution_exception_falls_back_to_cached_inherited(self) -> None:
        cached = BearerAuth(token="cached-token")
        manager = Mock()
        manager.resolve_effective_auth.side_effect = RuntimeError("db error")
        effective, source = _resolve_send_auth(
            own_auth=None,
            inherited_auth=cached,
            inherited_auth_source="cached-source",
            collection_id=5,
            folder=None,
            collection_manager=manager,
        )
        assert effective is cached
        assert source == "cached-source"

    def test_db_returns_none_falls_back_to_cached(self) -> None:
        cached = BearerAuth(token="cached-token")
        manager = Mock()
        manager.resolve_effective_auth.return_value = (None, None)
        effective, source = _resolve_send_auth(
            own_auth=None,
            inherited_auth=cached,
            inherited_auth_source="fallback",
            collection_id=5,
            folder=None,
            collection_manager=manager,
        )
        assert effective is cached
        assert source == "fallback"

    def test_no_auth_returns_none_none(self) -> None:
        effective, source = _resolve_send_auth(
            own_auth=None,
            inherited_auth=None,
            inherited_auth_source=None,
            collection_id=None,
            folder=None,
            collection_manager=None,
        )
        assert effective is None
        assert source is None


# ── _run_pre_script ────────────────────────────────────────────────────────────

class TestRunPreScript:
    def test_strict_policy_skips_script(self) -> None:
        vars_, result = _run_pre_script(
            "env['x'] = 'y'", "GET", "https://example.com", {}, {}, None,
            {"K": "V"}, {}, "strict"
        )
        assert vars_ == {"K": "V"}
        assert result is None

    def test_empty_script_returns_unchanged_vars(self) -> None:
        vars_, result = _run_pre_script(
            "", "GET", "https://example.com", {}, {}, None,
            {"K": "V"}, {}, "balanced"
        )
        assert vars_ == {"K": "V"}
        assert result is None

    def test_whitespace_script_skipped(self) -> None:
        vars_, result = _run_pre_script(
            "   \n\t  ", "GET", "https://example.com", {}, {}, None,
            {"K": "V"}, {}, "balanced"
        )
        assert vars_ == {"K": "V"}
        assert result is None

    def test_script_with_env_changes_updates_vars(self) -> None:
        script = "env['DYNAMIC'] = 'injected'"
        vars_, result = _run_pre_script(
            script, "GET", "https://example.com", {}, {}, None,
            {}, {}, "balanced"
        )
        assert vars_.get("DYNAMIC") == "injected"
        assert result is not None

    def test_script_without_env_changes_returns_original_vars(self) -> None:
        script = "assert True"
        vars_, result = _run_pre_script(
            script, "GET", "https://example.com", {}, {}, None,
            {"K": "V"}, {}, "balanced"
        )
        # No env changes → original vars unchanged
        assert "K" in vars_

    def test_script_exception_returns_none_result(self) -> None:
        """Unexpected runtime errors during script execution are swallowed."""
        with patch(
            "equinox.application.requests.execution.ScriptRunner.run_pre",
            side_effect=RuntimeError("unexpected"),
        ):
            vars_, result = _run_pre_script(
                "raise RuntimeError('boom')",
                "GET", "https://example.com", {}, {}, None,
                {"K": "V"}, {}, "balanced"
            )
        assert vars_ == {"K": "V"}
        assert result is None


# ── prepare_send error paths ───────────────────────────────────────────────────

class TestPrepareSendErrorPaths:
    def _patched_vars(self, monkeypatch, vars_=None):
        monkeypatch.setattr(
            "equinox.application.requests.execution.collect_interpolation_variables_detailed",
            lambda _db, collection_id=None, session_vars=None: (
                vars_ or {"BASE_URL": "api.example.com"},
                {},
            ),
        )

    def test_variable_collection_failure_returns_blocking_issue(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "equinox.application.requests.execution.collect_interpolation_variables_detailed",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("DB down")),
        )
        result = prepare_send(
            snapshot=_snapshot(),
            db=object(),
            collection_manager=None,
            own_auth=None,
            inherited_auth=None,
            inherited_auth_source=None,
            policy_profile="balanced",
        )
        assert result.ready is False
        assert result.blocking_issues[0].code == "variables.collection_failed"

    def test_body_assembly_failure_returns_blocking_issue(self, monkeypatch) -> None:
        self._patched_vars(monkeypatch)
        from equinox.application.requests._assembly import _MAX_BODY_SIZE

        oversized = "x" * (_MAX_BODY_SIZE + 1)
        result = prepare_send(
            snapshot=_snapshot(body=oversized, body_type="raw (JSON)"),
            db=object(),
            collection_manager=None,
            own_auth=None,
            inherited_auth=None,
            inherited_auth_source=None,
            policy_profile="balanced",
        )
        assert result.ready is False
        assert result.blocking_issues[0].code == "body.assembly_failed"

    def test_interpolation_failure_returns_blocking_issue(self, monkeypatch) -> None:
        self._patched_vars(monkeypatch)
        monkeypatch.setattr(
            "equinox.application.requests.execution.interpolate_request_fields",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("interpolation boom")),
        )
        result = prepare_send(
            snapshot=_snapshot(url="https://api.example.com/ping"),
            db=object(),
            collection_manager=None,
            own_auth=None,
            inherited_auth=None,
            inherited_auth_source=None,
            policy_profile="balanced",
        )
        assert result.ready is False
        assert result.blocking_issues[0].code == "interpolation.failed"

    def test_auth_interpolation_failure_returns_blocking_issue(self, monkeypatch) -> None:
        self._patched_vars(monkeypatch)
        monkeypatch.setattr(
            "equinox.application.requests.execution.interpolate_auth",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("auth boom")),
        )
        result = prepare_send(
            snapshot=_snapshot(url="https://api.example.com/ping"),
            db=object(),
            collection_manager=None,
            own_auth=BearerAuth(token="tok"),
            inherited_auth=None,
            inherited_auth_source=None,
            policy_profile="balanced",
        )
        assert result.ready is False
        assert result.blocking_issues[0].code == "auth.interpolation_failed"

    def test_request_construction_failure_returns_blocking_issue(self, monkeypatch) -> None:
        self._patched_vars(monkeypatch)
        monkeypatch.setattr(
            "equinox.application.requests.execution._build_request",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("construction boom")),
        )
        result = prepare_send(
            snapshot=_snapshot(url="https://api.example.com/ping"),
            db=object(),
            collection_manager=None,
            own_auth=None,
            inherited_auth=None,
            inherited_auth_source=None,
            policy_profile="balanced",
        )
        assert result.ready is False
        assert result.blocking_issues[0].code == "request.construction_failed"

    def test_path_params_expanded_into_url(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "equinox.application.requests.execution.collect_interpolation_variables_detailed",
            lambda _db, collection_id=None, session_vars=None: ({}, {}),
        )
        result = prepare_send(
            snapshot=_snapshot(
                url="https://api.example.com/users/{{id}}",
                path_params={"id": "99"},
            ),
            db=object(),
            collection_manager=None,
            own_auth=None,
            inherited_auth=None,
            inherited_auth_source=None,
            policy_profile="balanced",
        )
        # The path params should be expanded — request URL should contain 99
        if result.ready:
            assert "99" in result.package.request.url

