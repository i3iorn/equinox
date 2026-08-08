from __future__ import annotations

from dataclasses import dataclass

from equinox.application.requests.post_processing import (
    apply_captures,
    build_deferred_persistence_plan,
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

    @property
    def text(self) -> str:
        return self.body_text

    def json(self):
        import json

        return json.loads(self.body_text)


def test_apply_captures_returns_session_updates_and_display_lines() -> None:
    req = type(
        "Req",
        (),
        {"captures": [{"variable": "token", "source": "json", "path": "token"}]},
    )()
    resp = _ResponseStub(200, {"content-type": "application/json"}, '{"token":"abc"}', req)

    outcome = apply_captures(resp)

    assert outcome.error is None
    assert outcome.session_updates == {"token": "abc"}
    assert len(outcome.display_lines) == 1
    assert "token" in outcome.display_lines[0]


def test_run_post_script_skips_in_strict_policy() -> None:
    req = type("Req", (), {"captures": []})()
    resp = _ResponseStub(200, {}, "{}", req)

    outcome = run_post_script(
        policy_profile="strict",
        post_script="env['x'] = '1'",
        response=resp,
        session_vars={},
    )

    assert outcome.skipped is True
    assert outcome.skip_message == "Skipped by strict policy"
    assert outcome.script_result is None


def test_run_post_script_returns_error_when_script_raises() -> None:
    req = type("Req", (), {"captures": []})()
    resp = _ResponseStub(200, {}, "{}", req)

    outcome = run_post_script(
        policy_profile="balanced",
        post_script="raise RuntimeError('bad script')",
        response=resp,
        session_vars={},
    )

    assert outcome.skipped is False
    assert outcome.error is None
    assert outcome.script_result is not None
    assert outcome.script_result.error == "bad script"


def test_build_deferred_persistence_plan_sets_expected_flags() -> None:
    own = OAuth2Auth(token_url="https://idp/token", client_id="cid")
    own.access_token = "own-token"

    inherited = OAuth2Auth(token_url="https://idp/token", client_id="cid")
    inherited.access_token = "inh-token"

    request = type("Req", (), {"id": 10, "collection_id": 7})()
    plan = build_deferred_persistence_plan(
        request=request,
        error_message=None,
        send_inherited_auth=inherited,
        send_inherited_source="collection",
        own_auth=own,
    )

    assert plan.save_history is True
    assert plan.persist_inherited_token is True
    assert plan.persist_own_oauth2_token is True


def test_build_error_handling_plan_includes_hint_and_deferred_plan() -> None:
    request = type("Req", (), {"id": 10, "collection_id": 7})()
    error = type(
        "Err",
        (),
        {
            "message": "boom",
            "exc_type": "RuntimeError",
            "hint": "try again",
            "tb": "trace",
        },
    )()
    inherited = OAuth2Auth(token_url="https://idp/token", client_id="cid")
    inherited.access_token = "inh-token"

    plan = build_error_handling_plan(
        error=error,
        request=request,
        log_file_path="C:/tmp/equinox.log",
        send_inherited_auth=inherited,
        send_inherited_source="collection",
        own_auth=None,
    )

    assert plan.status_message == "Error: boom"
    assert plan.dialog_title == "Request Failed — RuntimeError"
    assert "try again" in plan.dialog_text
    assert "Full details in:" in plan.dialog_text
    assert plan.copy_text == "trace"
    assert plan.deferred_plan.save_history is True
    assert plan.deferred_plan.persist_inherited_token is True


def test_build_success_handling_plan_formats_status_and_url() -> None:
    request = type("Req", (), {"url": "https://api.example.com/x", "id": 1, "collection_id": 2})()
    response = type(
        "Resp",
        (),
        {"elapsed": 0.125, "status_code": 201, "reason": "Created", "request": request},
    )()

    plan = build_success_handling_plan(
        response=response,
        send_inherited_auth=None,
        send_inherited_source=None,
        own_auth=None,
    )

    assert plan.elapsed_ms == 125
    assert plan.status_message == "201 Created  —  125 ms"
    assert plan.completer_url == "https://api.example.com/x"
    assert plan.deferred_plan.save_history is True
