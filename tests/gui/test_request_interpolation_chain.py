"""Regression tests for chained URL/path-param interpolation in send mixin."""

from equinox.gui.request_panel.mixins._send_mixin import _RequestSendMixin


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

