"""Regression tests for chained URL/path-param interpolation in send mixin."""

from types import SimpleNamespace

from equinox.gui.request_panel.mixins._send_mixin import _RequestSendMixin
import equinox.gui.request_panel.mixins._send_mixin as send_mixin_mod


def test_resolve_path_params_supports_chained_values() -> None:
    variables = {"USER_ID": "42"}
    path_params = {
        "entity": "{{USER_ID}}",
        "resource": "{{entity}}",
    }

    resolved = _RequestSendMixin._resolve_path_params(path_params, variables)

    assert resolved == {
        "entity": "42",
        "resource": "42",
    }


def test_interpolate_request_fields_uses_resolved_path_params_in_url_and_query() -> None:
    url, headers, params, body, path_params = _RequestSendMixin._interpolate_request_fields(
        url="https://api.example.com/{{resource}}",
        headers={"X-Entity": "{{entity}}"},
        params={"id": "{{resource}}", "owner": "{{USER_ID}}"},
        body='{"ref":"{{resource}}"}',
        path_params={"entity": "{{USER_ID}}", "resource": "{{entity}}"},
        variables={"USER_ID": "42"},
    )

    assert url == "https://api.example.com/42"
    assert headers == {"X-Entity": "42"}
    assert params == {"id": "42", "owner": "42"}
    assert body == '{"ref":"42"}'
    assert path_params == {"entity": "42", "resource": "42"}


def test_resolve_path_params_allows_chained_key_interpolation() -> None:
    resolved = _RequestSendMixin._resolve_path_params(
        path_params={"{{PKEY}}": "{{PVAL}}", "suffix": "{{PKEY}}-ok"},
        variables={"PKEY": "id", "PVAL": "abc"},
    )

    assert resolved["id"] == "abc"
    assert resolved["suffix"] == "id-ok"


def test_resolve_path_params_uses_global_value_for_self_referential_key() -> None:
    resolved = _RequestSendMixin._resolve_path_params(
        path_params={"BASE_URL": "{{BASE_URL}}"},
        variables={"BASE_URL": "api.example.com"},
    )

    assert resolved["BASE_URL"] == "api.example.com"


def test_interpolate_request_fields_resolves_base_url_case_mismatch() -> None:
    url, headers, params, body, path_params = _RequestSendMixin._interpolate_request_fields(
        url="https://{{BASE_URL}}/livez",
        headers={},
        params={},
        body=None,
        path_params={},
        variables={"base_url": "api.example.com"},
    )

    assert url == "https://api.example.com/livez"
    assert headers == {}
    assert params == {}
    assert body is None
    assert path_params == {}


def test_collect_unresolved_placeholders_scans_all_request_fields() -> None:
    unresolved = _RequestSendMixin._collect_unresolved_placeholders(
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


def test_send_request_blocks_dispatch_when_placeholders_still_unresolved(monkeypatch) -> None:
    warned = []

    class _FakeLineEdit:
        def __init__(self, text: str) -> None:
            self._text = text

        def text(self) -> str:
            return self._text

        def setEnabled(self, _enabled: bool) -> None:
            return None

    class _FakeCombo:
        def currentText(self) -> str:
            return "GET"

        def setEnabled(self, _enabled: bool) -> None:
            return None

    class _FakeButton:
        def setEnabled(self, _enabled: bool) -> None:
            return None

        def setVisible(self, _visible: bool) -> None:
            return None

        def setText(self, _text: str) -> None:
            return None

    class _Harness(_RequestSendMixin):
        def __init__(self) -> None:
            self.url_input = _FakeLineEdit("https://{{BASE_URL}}/livez")
            self.method_combo = _FakeCombo()
            self.send_button = _FakeButton()
            self.cancel_button = _FakeButton()
            self._elapsed_timer = SimpleNamespace(start=lambda: None, stop=lambda: None)
            self._worker = None
            self.db = object()
            self.current_request = SimpleNamespace(collection_id=25)
            self._session_vars = {}
            self.request_sent = SimpleNamespace(emit=lambda _req: None)

        def _display_preflight_warnings(self) -> None:
            return None

        def _gather_request_fields(self):
            return "GET", {}, {}, [], None, "None", None, {}

        def _run_pre_script(self, method, url, headers, params, body, variables):
            return variables

        def _resolve_send_auth(self):
            raise AssertionError("auth resolution should not run when variables are unresolved")

    monkeypatch.setattr(
        send_mixin_mod,
        "collect_interpolation_variables_detailed",
        lambda _db, collection_id=None, session_vars=None: (
            {"BASE_URL": "{{BASE_URL}}"},
            {"BASE_URL": "collection"},
        ),
    )
    monkeypatch.setattr(
        send_mixin_mod.QMessageBox,
        "warning",
        lambda _parent, _title, message: warned.append(message),
    )

    _Harness()._send_request()

    assert warned
    assert "Unresolved placeholders" in warned[0]
    assert "BASE_URL(source=collection, value_type=str, value_is_template=True)" in warned[0]


def test_resolve_proxy_url_handles_invalid_port(monkeypatch) -> None:
    class _FakeSettings:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def value(self, key):
            if key == "proxy/host":
                return "localhost"
            if key == "proxy/port":
                return "not-a-port"
            return None

    monkeypatch.setattr(send_mixin_mod, "_QSettings", None, raising=False)
    import PyQt6.QtCore as qt_core
    monkeypatch.setattr(qt_core, "QSettings", _FakeSettings)

    assert _RequestSendMixin._resolve_proxy_url() is None


