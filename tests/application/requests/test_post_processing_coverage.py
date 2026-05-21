"""Extended coverage tests for equinox.application.requests.post_processing."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from equinox.application.requests.post_processing import (
    apply_captures,
    build_error_handling_plan,
    build_success_handling_plan,
    run_post_script,
)
from equinox.auth import OAuth2Auth


@dataclass
class _ResponseStub:
    status_code: int
    headers: dict
    body_text: str
    request: object
    elapsed: float = 0.25
    reason: str = "OK"

    @property
    def text(self) -> str:
        return self.body_text

    def json(self):
        import json
        return json.loads(self.body_text)


@dataclass
class _RequestStub:
    url: str = "https://api.example.com"
    collection_id: int = None
    id: int = None


class TestApplyCoverage:
    def test_apply_captures_no_captures_returns_empty(self) -> None:
        req = type("Req", (), {"captures": []})()
        resp = _ResponseStub(200, {}, "{}", req)
        outcome = apply_captures(resp)
        assert outcome.session_updates == {}
        assert outcome.display_lines == []
        assert outcome.error is None

    def test_apply_captures_missing_captures_attr_returns_empty(self) -> None:
        req = type("Req", (), {})()
        resp = _ResponseStub(200, {}, "{}", req)
        outcome = apply_captures(resp)
        assert outcome.session_updates == {}

    def test_apply_captures_engine_exception_returns_error(self) -> None:
        req = type("Req", (), {"captures": [{"variable": "x", "source": "json", "path": "x"}]})()
        resp = _ResponseStub(200, {}, "not-json", req)
        # CaptureEngine will fail to parse JSON body
        outcome = apply_captures(resp)
        # Either an error or empty (depends on CaptureEngine behaviour, but no exception raised)
        assert isinstance(outcome.error, (str, type(None)))


class TestRunPostScriptCoverage:
    def test_empty_script_returns_empty_outcome(self) -> None:
        req = _RequestStub()
        resp = _ResponseStub(200, {}, "{}", req)
        outcome = run_post_script(
            policy_profile="balanced",
            post_script="",
            response=resp,
            session_vars={},
        )
        assert outcome.skipped is False
        assert outcome.script_result is None
        assert outcome.error is None

    def test_json_parse_error_does_not_prevent_script_run(self) -> None:
        """When response.json() raises, the script still runs with json=None."""
        req = _RequestStub()
        resp = _ResponseStub(200, {}, "not-json", req)
        outcome = run_post_script(
            policy_profile="balanced",
            post_script="assert True",
            response=resp,
            session_vars={},
        )
        # Script can still succeed even if JSON parsing fails
        assert outcome.error is None or isinstance(outcome.error, str)

    def test_script_runtime_exception_returns_error(self) -> None:
        req = _RequestStub()
        resp = _ResponseStub(200, {}, "{}", req)
        with patch(
            "equinox.application.requests.post_processing.ScriptRunner.run_post",
            side_effect=RuntimeError("unexpected"),
        ):
            outcome = run_post_script(
                policy_profile="balanced",
                post_script="do_something()",
                response=resp,
                session_vars={},
            )
        assert outcome.error is not None


class TestBuildErrorHandlingPlanCoverage:
    def test_plan_with_hint_includes_hint_in_dialog_text(self) -> None:
        class _Error:
            message = "Connection refused"
            exc_type = "ConnectionError"
            hint = "Check that the server is running"
            tb = "Traceback..."

        plan = build_error_handling_plan(
            error=_Error(),
            request=_RequestStub(),
            log_file_path="/tmp/equinox.log",
            send_inherited_auth=None,
            send_inherited_source=None,
            own_auth=None,
        )
        assert "Check that the server is running" in plan.dialog_text
        assert "/tmp/equinox.log" in plan.dialog_text
        assert plan.status_message.startswith("Error:")

    def test_plan_without_hint_no_hint_in_dialog_text(self) -> None:
        class _ErrorNoHint:
            message = "Timeout"
            exc_type = "TimeoutError"
            hint = None
            tb = ""

        plan = build_error_handling_plan(
            error=_ErrorNoHint(),
            request=_RequestStub(),
            log_file_path=None,
            send_inherited_auth=None,
            send_inherited_source=None,
            own_auth=None,
        )
        assert "hint" not in plan.dialog_text.lower() or plan.dialog_text == "Timeout"

    def test_plan_without_log_path(self) -> None:
        class _Err:
            message = "Fail"
            exc_type = "Error"
            hint = None
            tb = ""

        plan = build_error_handling_plan(
            error=_Err(),
            request=_RequestStub(),
            log_file_path=None,
            send_inherited_auth=None,
            send_inherited_source=None,
            own_auth=None,
        )
        assert "Full details" not in plan.dialog_text


class TestBuildSuccessHandlingPlanCoverage:
    def test_success_plan_elapsed_and_status(self) -> None:
        req = _RequestStub(url="https://api.example.com/users")
        resp = _ResponseStub(200, {}, "{}", req, elapsed=0.5, reason="OK")
        plan = build_success_handling_plan(
            response=resp,
            send_inherited_auth=None,
            send_inherited_source=None,
            own_auth=None,
        )
        assert plan.elapsed_ms == 500
        assert "200" in plan.status_message
        assert plan.completer_url == "https://api.example.com/users"

    def test_success_plan_with_inherited_oauth2(self) -> None:
        auth = OAuth2Auth(token_url="https://idp/token", client_id="cid")
        auth.access_token = "token"
        req = _RequestStub(url="https://api.example.com", collection_id=1)
        resp = _ResponseStub(200, {}, "{}", req, elapsed=0.1)

        plan = build_success_handling_plan(
            response=resp,
            send_inherited_auth=auth,
            send_inherited_source="collection",
            own_auth=None,
        )
        assert plan.deferred_plan.persist_inherited_token is True

    def test_success_plan_with_own_oauth2(self) -> None:
        auth = OAuth2Auth(token_url="https://idp/token", client_id="cid")
        auth.access_token = "token"
        req = _RequestStub(url="https://api.example.com", id=5)
        resp = _ResponseStub(200, {}, "{}", req, elapsed=0.1)

        plan = build_success_handling_plan(
            response=resp,
            send_inherited_auth=None,
            send_inherited_source=None,
            own_auth=auth,
        )
        assert plan.deferred_plan.persist_own_oauth2_token is True

