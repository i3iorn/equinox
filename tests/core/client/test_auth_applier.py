"""100% coverage tests for equinox.core.client.auth_applier"""

import logging
from typing import Optional
import pytest
from unittest.mock import MagicMock

from equinox.auth.base import AuthStrategy
from equinox.core.client.auth_applier import (
    AuthApplier,
    _is_proxy_connection_refused,
    _PROXY_REFUSED_MARKERS,
)
from equinox.core.exceptions import RequestError
from equinox.core.request import Request


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_request(**kwargs) -> Request:
    """Return a minimal Request; override fields via kwargs."""
    defaults = dict(method="GET", url="https://example.com")
    defaults.update(kwargs)
    return Request(**defaults)


def _make_auth(adds_headers: Optional[dict] = None) -> MagicMock:
    """Return a mock AuthStrategy whose apply() populates *adds_headers*."""
    auth = MagicMock(spec=AuthStrategy)

    def _apply(request, headers):
        if adds_headers:
            headers.update(adds_headers)

    auth.apply.side_effect = _apply
    return auth


# ---------------------------------------------------------------------------
# _is_proxy_connection_refused
# ---------------------------------------------------------------------------

class TestIsProxyConnectionRefused:
    @pytest.mark.parametrize("marker", list(_PROXY_REFUSED_MARKERS))
    def test_returns_true_for_each_marker(self, marker):
        assert _is_proxy_connection_refused(f"some error: {marker} occurred")

    def test_case_insensitive_upper(self):
        assert _is_proxy_connection_refused("Connection Refused by proxy")

    def test_case_insensitive_mixed(self):
        assert _is_proxy_connection_refused("ECONNREFUSED")

    def test_returns_false_when_no_marker(self):
        assert not _is_proxy_connection_refused("network timeout")

    def test_empty_string_returns_false(self):
        assert not _is_proxy_connection_refused("")

    def test_10061_marker(self):
        assert _is_proxy_connection_refused("WinError 10061 target machine refused")


# ---------------------------------------------------------------------------
# AuthApplier.apply — no auth
# ---------------------------------------------------------------------------

class TestAuthApplierApplyNoAuth:
    def test_returns_empty_dict_when_no_auth(self):
        applier = AuthApplier()
        request = _make_request()
        headers: dict = {}
        result = applier.apply(request, headers, explicit_auth=None, proxy=None)
        assert result == {}

    def test_does_not_modify_headers_when_no_auth(self):
        applier = AuthApplier()
        request = _make_request()
        headers = {"Accept": "application/json"}
        applier.apply(request, headers, explicit_auth=None, proxy=None)
        assert headers == {"Accept": "application/json"}

    def test_logs_debug_when_no_auth(self, caplog):
        applier = AuthApplier()
        request = _make_request()
        with caplog.at_level(logging.DEBUG, logger="equinox.core.client.auth_applier"):
            applier.apply(request, {}, explicit_auth=None, proxy=None)
        assert "No auth strategy" in caplog.text


# ---------------------------------------------------------------------------
# AuthApplier.apply — auth resolution precedence
# ---------------------------------------------------------------------------

class TestAuthApplierApplyPrecedence:
    def test_explicit_auth_takes_precedence_over_request_auth(self):
        applier = AuthApplier()
        request_auth = _make_auth({"X-From-Request": "yes"})
        explicit_auth = _make_auth({"X-From-Explicit": "yes"})
        request = _make_request(auth=request_auth)
        headers: dict = {}
        result = applier.apply(request, headers, explicit_auth=explicit_auth, proxy=None)
        assert "X-From-Explicit" in result
        assert "X-From-Request" not in result
        request_auth.apply.assert_not_called()

    def test_request_auth_used_when_no_explicit_auth(self):
        applier = AuthApplier()
        auth = _make_auth({"Authorization": "Bearer tok"})
        request = _make_request(auth=auth)
        headers: dict = {}
        result = applier.apply(request, headers, explicit_auth=None, proxy=None)
        assert result == {"Authorization": "Bearer tok"}


# ---------------------------------------------------------------------------
# AuthApplier.apply — header isolation
# ---------------------------------------------------------------------------

class TestAuthApplierApplyHeaderIsolation:
    def test_only_added_headers_returned(self):
        applier = AuthApplier()
        auth = _make_auth({"Authorization": "Bearer tok", "X-Extra": "val"})
        request = _make_request()
        headers = {"Content-Type": "application/json"}
        result = applier.apply(request, headers, explicit_auth=auth, proxy=None)
        assert set(result.keys()) == {"Authorization", "X-Extra"}
        assert "Content-Type" not in result

    def test_pre_existing_header_not_in_result(self):
        applier = AuthApplier()
        # strategy overwrites an existing header
        auth = _make_auth({"Existing": "overwritten"})
        request = _make_request()
        headers = {"Existing": "original"}
        result = applier.apply(request, headers, explicit_auth=auth, proxy=None)
        # The key was already present — not in added_keys
        assert "Existing" not in result

    def test_returns_empty_dict_when_strategy_adds_nothing(self):
        applier = AuthApplier()
        auth = _make_auth(adds_headers=None)
        request = _make_request()
        result = applier.apply(request, {}, explicit_auth=auth, proxy=None)
        assert result == {}

    def test_headers_dict_mutated_with_added_headers(self):
        applier = AuthApplier()
        auth = _make_auth({"Authorization": "Bearer tok"})
        request = _make_request()
        headers: dict = {}
        applier.apply(request, headers, explicit_auth=auth, proxy=None)
        assert headers["Authorization"] == "Bearer tok"


# ---------------------------------------------------------------------------
# AuthApplier.apply — logging on success
# ---------------------------------------------------------------------------

class TestAuthApplierApplyLogging:
    def test_debug_log_when_headers_added(self, caplog):
        applier = AuthApplier()
        auth = _make_auth({"Authorization": "Bearer tok"})
        request = _make_request()
        with caplog.at_level(logging.DEBUG, logger="equinox.core.client.auth_applier"):
            applier.apply(request, {}, explicit_auth=auth, proxy=None)
        assert "Auth applied" in caplog.text

    def test_no_auth_applied_log_when_nothing_added(self, caplog):
        applier = AuthApplier()
        auth = _make_auth(adds_headers=None)
        request = _make_request()
        with caplog.at_level(logging.DEBUG, logger="equinox.core.client.auth_applier"):
            applier.apply(request, {}, explicit_auth=auth, proxy=None)
        assert "Auth applied" not in caplog.text


# ---------------------------------------------------------------------------
# AuthApplier._invoke_strategy — proxy injection
# ---------------------------------------------------------------------------

class TestInvokeStrategyProxyInjection:
    def test_proxy_injected_when_strategy_has_proxy_attr(self):
        applier = AuthApplier()
        auth = _make_auth()
        auth._proxy = None  # signal that attribute exists
        request = _make_request()
        applier._invoke_strategy(auth, request, {}, proxy="http://proxy:3128")
        assert auth._proxy == "http://proxy:3128"

    def test_proxy_not_injected_when_strategy_lacks_proxy_attr(self):
        applier = AuthApplier()
        auth = _make_auth()
        # No _proxy attribute on the mock
        assert not hasattr(auth, "_proxy")
        request = _make_request()
        # Should not raise
        applier._invoke_strategy(auth, request, {}, proxy="http://proxy:3128")

    def test_proxy_none_skips_injection(self):
        applier = AuthApplier()
        auth = _make_auth()
        auth._proxy = "old"
        request = _make_request()
        applier._invoke_strategy(auth, request, {}, proxy=None)
        # _proxy must remain unchanged
        assert auth._proxy == "old"

    def test_strategy_apply_called_with_request_and_headers(self):
        applier = AuthApplier()
        auth = _make_auth()
        request = _make_request()
        headers: dict = {}
        applier._invoke_strategy(auth, request, headers, proxy=None)
        auth.apply.assert_called_once_with(request, headers)

    def test_exception_in_apply_raises_request_error(self):
        applier = AuthApplier()
        auth = MagicMock(spec=AuthStrategy)
        auth.apply.side_effect = RuntimeError("boom")
        request = _make_request()
        with pytest.raises(RequestError):
            applier._invoke_strategy(auth, request, {}, proxy=None)

    def test_invoke_strategy_logs_debug(self, caplog):
        applier = AuthApplier()
        auth = _make_auth()
        request = _make_request()
        with caplog.at_level(logging.DEBUG, logger="equinox.core.client.auth_applier"):
            applier._invoke_strategy(auth, request, {}, proxy=None)
        assert "Applying auth strategy" in caplog.text


# ---------------------------------------------------------------------------
# AuthApplier._map_auth_error
# ---------------------------------------------------------------------------

class TestMapAuthError:
    def _dummy_strategy(self):
        return MagicMock(spec=AuthStrategy)

    def test_no_proxy_returns_generic_request_error(self):
        strategy = self._dummy_strategy()
        exc = ValueError("bad creds")
        err = AuthApplier._map_auth_error(exc, strategy, proxy=None)
        assert isinstance(err, RequestError)
        assert "Authentication failed" in str(err)
        assert "proxy" not in str(err).lower()

    def test_proxy_not_refused_returns_generic_error(self):
        strategy = self._dummy_strategy()
        exc = ValueError("some other network error")
        err = AuthApplier._map_auth_error(exc, strategy, proxy="http://proxy:3128")
        assert isinstance(err, RequestError)
        assert "proxy" not in str(err).lower()

    def test_proxy_connection_refused_returns_proxy_hint(self):
        strategy = self._dummy_strategy()
        exc = OSError("connection refused by remote host")
        err = AuthApplier._map_auth_error(exc, strategy, proxy="http://proxy:3128")
        assert isinstance(err, RequestError)
        assert "proxy" in str(err).lower()
        assert "http://proxy:3128" in str(err)

    def test_proxy_refused_10061_returns_proxy_hint(self):
        strategy = self._dummy_strategy()
        exc = OSError("WinError 10061")
        err = AuthApplier._map_auth_error(exc, strategy, proxy="http://myproxy:8080")
        assert "http://myproxy:8080" in str(err)

    def test_error_details_contain_proxy_and_strategy(self):
        strategy = self._dummy_strategy()
        type(strategy).__name__ = "FakeAuth"
        exc = OSError("econnrefused")
        err = AuthApplier._map_auth_error(exc, strategy, proxy="http://p:1")
        assert err.details["proxy"] == "http://p:1"

    def test_logs_error(self, caplog):
        strategy = self._dummy_strategy()
        exc = RuntimeError("cred fail")
        with caplog.at_level(logging.ERROR, logger="equinox.core.client.auth_applier"):
            AuthApplier._map_auth_error(exc, strategy, proxy=None)
        assert "Authentication failed" in caplog.text

    def test_exception_chained_to_request_error_via_invoke(self):
        """_invoke_strategy raises RequestError chained from the original exc."""
        applier = AuthApplier()
        auth = MagicMock(spec=AuthStrategy)
        original = RuntimeError("original")
        auth.apply.side_effect = original
        request = _make_request()
        with pytest.raises(RequestError) as exc_info:
            applier._invoke_strategy(auth, request, {}, proxy=None)
        assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# Full apply() path when strategy raises
# ---------------------------------------------------------------------------

class TestAuthApplierApplyStrategyRaises:
    def test_apply_propagates_request_error_on_strategy_failure(self):
        applier = AuthApplier()
        auth = MagicMock(spec=AuthStrategy)
        auth.apply.side_effect = RuntimeError("token expired")
        request = _make_request()
        with pytest.raises(RequestError, match="Authentication failed"):
            applier.apply(request, {}, explicit_auth=auth, proxy=None)

    def test_apply_proxy_hint_surfaced_on_connection_refused(self):
        applier = AuthApplier()
        auth = MagicMock(spec=AuthStrategy)
        auth.apply.side_effect = OSError("connection refused")
        request = _make_request()
        with pytest.raises(RequestError) as exc_info:
            applier.apply(
                request, {}, explicit_auth=auth, proxy="http://proxy:3128"
            )
        assert "proxy" in str(exc_info.value).lower()



