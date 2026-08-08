from __future__ import annotations

from dataclasses import dataclass

from equinox.application.requests.execution import prepare_send
from equinox.application.requests.models import RequestEditorSnapshot
from equinox.auth import BearerAuth


@dataclass
class _FakeCollectionManager:
    inherited_auth: object
    source: str

    def resolve_effective_auth(self, _request):
        return self.inherited_auth, self.source


def _snapshot(**overrides) -> RequestEditorSnapshot:
    data = {
        "method": "GET",
        "url": "https://{{BASE_URL}}/users/{{RID}}",
        "collection_id": 7,
        "folder": "",
        "request_id": 11,
        "pre_script": "",
    }
    data.update(overrides)
    return RequestEditorSnapshot(**data)


def test_prepare_send_builds_transport_ready_request(monkeypatch) -> None:
    monkeypatch.setattr(
        "equinox.application.requests.execution.collect_interpolation_variables_detailed",
        lambda _db, collection_id=None, session_vars=None: (
            {"BASE_URL": "api.example.com", "RID": "42"},
            {"BASE_URL": "collection", "RID": "session"},
        ),
    )

    collection_mgr = _FakeCollectionManager(BearerAuth(token="col-token"), "collection")
    result = prepare_send(
        snapshot=_snapshot(),
        db=object(),
        collection_manager=collection_mgr,
        own_auth=None,
        inherited_auth=None,
        inherited_auth_source=None,
        policy_profile="balanced",
    )

    assert result.ready is True
    assert result.package is not None
    assert result.package.request.url == "https://api.example.com/users/42"
    assert result.package.request.collection_id == 7
    assert isinstance(result.package.request.auth, BearerAuth)
    assert result.package.inherited_auth_source == "collection"
    assert result.package.is_auth_inherited is True


def test_prepare_send_blocks_on_unresolved_placeholders(monkeypatch) -> None:
    monkeypatch.setattr(
        "equinox.application.requests.execution.collect_interpolation_variables_detailed",
        lambda _db, collection_id=None, session_vars=None: (
            {"BASE_URL": "{{BASE_URL}}"},
            {"BASE_URL": "collection"},
        ),
    )

    result = prepare_send(
        snapshot=_snapshot(url="https://{{BASE_URL}}/livez"),
        db=object(),
        collection_manager=None,
        own_auth=None,
        inherited_auth=None,
        inherited_auth_source=None,
        policy_profile="balanced",
    )

    assert result.ready is False
    assert result.blocking_issues
    assert result.blocking_issues[0].code == "variables.unresolved"
    assert "value_is_template=True" in result.blocking_issues[0].message


def test_prepare_send_skips_pre_script_when_policy_is_strict(monkeypatch) -> None:
    monkeypatch.setattr(
        "equinox.application.requests.execution.collect_interpolation_variables_detailed",
        lambda _db, collection_id=None, session_vars=None: (
            {"BASE_URL": "api.example.com", "RID": "42"},
            {"BASE_URL": "collection", "RID": "session"},
        ),
    )

    monkeypatch.setattr(
        "equinox.application.requests.execution.ScriptRunner.run_pre",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("run_pre must not run in strict mode"),
        ),
    )

    result = prepare_send(
        snapshot=_snapshot(pre_script="env['x'] = 'y'"),
        db=object(),
        collection_manager=None,
        own_auth=None,
        inherited_auth=None,
        inherited_auth_source=None,
        policy_profile="strict",
    )

    assert result.ready is True
    assert result.package is not None
    assert result.package.pre_script_result is None
