"""Tests for ResourceLimits validation and PluginSandbox enforcement."""

from __future__ import annotations

import time

import pytest

from equinox.core.exceptions import SecurityError
from equinox.plugins.security import (
    Permission,
    PluginManifest,
    PluginSandbox,
    ResourceLimits,
    SecureHTTPClientProxy,
    SecurePluginContext,
    SecureStorageProxy,
)


def _manifest(
    name: str = "test-plugin",
    perms: set[Permission] | None = None,
) -> PluginManifest:
    return PluginManifest(
        name=name,
        version="1.0",
        author="test",
        permissions=perms or set(),
    )


# ── ResourceLimits ────────────────────────────────────────────────────────────


class TestResourceLimits:
    def test_defaults_are_valid(self) -> None:
        rl = ResourceLimits()
        assert rl.max_memory_mb == 100
        assert rl.max_execution_time_ms == 5000
        assert rl.max_network_requests == 100

    def test_memory_below_minimum_raises(self) -> None:
        with pytest.raises(ValueError, match="max_memory_mb"):
            ResourceLimits(max_memory_mb=0)  # type: ignore[call-overload]

    def test_memory_above_maximum_raises(self) -> None:
        with pytest.raises(ValueError, match="max_memory_mb"):
            ResourceLimits(max_memory_mb=2000)

    def test_execution_time_below_minimum_raises(self) -> None:
        with pytest.raises(ValueError, match="max_execution_time_ms"):
            ResourceLimits(max_execution_time_ms=50)

    def test_execution_time_above_maximum_raises(self) -> None:
        with pytest.raises(ValueError, match="max_execution_time_ms"):
            ResourceLimits(max_execution_time_ms=70000)

    def test_boundary_values_accepted(self) -> None:
        rl = ResourceLimits(max_memory_mb=1, max_execution_time_ms=100)
        assert rl.max_memory_mb == 1
        assert rl.max_execution_time_ms == 100

        rl2 = ResourceLimits(max_memory_mb=1000, max_execution_time_ms=60000)
        assert rl2.max_memory_mb == 1000


# ── PluginSandbox permission checks ──────────────────────────────────────────


class TestSandboxPermissions:
    def test_check_permission_passes_when_granted(self) -> None:
        sandbox = PluginSandbox(_manifest(perms={Permission.NETWORK_HTTP}))
        sandbox.check_permission(Permission.NETWORK_HTTP)  # no exception

    def test_check_permission_raises_when_missing(self) -> None:
        sandbox = PluginSandbox(_manifest(perms=set()))
        with pytest.raises(SecurityError, match="missing permission"):
            sandbox.check_permission(Permission.NETWORK_HTTP)

    def test_check_permission_error_contains_plugin_name(self) -> None:
        sandbox = PluginSandbox(_manifest(name="named-plugin"))
        with pytest.raises(SecurityError, match="named-plugin"):
            sandbox.check_permission(Permission.STORAGE_READ)


# ── Resource limit enforcement ────────────────────────────────────────────────


class TestSandboxNetworkLimit:
    def test_allows_up_to_limit(self) -> None:
        sandbox = PluginSandbox(_manifest(), limits=ResourceLimits(max_network_requests=3))
        sandbox.check_network_request()
        sandbox.check_network_request()
        sandbox.check_network_request()  # exactly at limit

    def test_exceeds_limit_raises(self) -> None:
        sandbox = PluginSandbox(_manifest(), limits=ResourceLimits(max_network_requests=2))
        sandbox.check_network_request()
        sandbox.check_network_request()
        with pytest.raises(SecurityError, match="network request limit"):
            sandbox.check_network_request()


class TestSandboxStorageLimit:
    def test_exceeds_limit_raises(self) -> None:
        sandbox = PluginSandbox(_manifest(), limits=ResourceLimits(max_storage_operations=1))
        sandbox.check_storage_operation()
        with pytest.raises(SecurityError, match="storage operation limit"):
            sandbox.check_storage_operation()


class TestSandboxExecutionTime:
    def test_no_start_time_is_noop(self) -> None:
        sandbox = PluginSandbox(_manifest())
        sandbox.check_execution_time()  # no exception

    def test_within_time_limit(self) -> None:
        sandbox = PluginSandbox(
            _manifest(),
            limits=ResourceLimits(max_execution_time_ms=5000),
        )
        sandbox.start_execution()
        sandbox.check_execution_time()  # should pass immediately

    def test_exceeds_time_limit(self) -> None:
        sandbox = PluginSandbox(
            _manifest(),
            limits=ResourceLimits(max_execution_time_ms=100),
        )
        sandbox.start_execution()
        time.sleep(0.15)
        with pytest.raises(SecurityError, match="execution time limit"):
            sandbox.check_execution_time()


class TestSandboxFileSize:
    def test_within_limit(self) -> None:
        sandbox = PluginSandbox(_manifest(), limits=ResourceLimits(max_file_size_mb=1))
        sandbox.check_file_size(1024)  # 1KB

    def test_exceeds_limit(self) -> None:
        sandbox = PluginSandbox(_manifest(), limits=ResourceLimits(max_file_size_mb=1))
        with pytest.raises(SecurityError, match="File size"):
            sandbox.check_file_size(2 * 1024 * 1024 + 1)


# ── Sandbox stats and lifecycle ──────────────────────────────────────────────


class TestSandboxStats:
    def test_initial_stats(self) -> None:
        sandbox = PluginSandbox(_manifest(name="stats-plugin"))
        stats = sandbox.get_stats()
        assert stats["plugin"] == "stats-plugin"
        assert stats["network_requests"] == 0
        assert stats["storage_operations"] == 0

    def test_stats_track_usage(self) -> None:
        sandbox = PluginSandbox(_manifest(), limits=ResourceLimits(max_network_requests=5))
        sandbox.check_network_request()
        sandbox.check_network_request()
        stats = sandbox.get_stats()
        assert stats["network_requests"] == 2

    def test_reset_counters(self) -> None:
        sandbox = PluginSandbox(_manifest(), limits=ResourceLimits(max_network_requests=10))
        sandbox.check_network_request()
        sandbox.check_storage_operation()
        sandbox.reset_counters()
        stats = sandbox.get_stats()
        assert stats["network_requests"] == 0
        assert stats["storage_operations"] == 0


class TestSandboxExecutionTracking:
    def test_start_and_end_execution(self) -> None:
        sandbox = PluginSandbox(_manifest())
        sandbox.start_execution()
        assert sandbox._start_time is not None
        sandbox.end_execution()
        assert sandbox._start_time is None

    def test_end_execution_without_start(self) -> None:
        sandbox = PluginSandbox(_manifest())
        sandbox.end_execution()  # no exception


# ── SecurePluginContext ───────────────────────────────────────────────────────


class TestSecurePluginContext:
    def _ctx(self, perms: set[Permission]) -> SecurePluginContext:
        sandbox = PluginSandbox(_manifest(perms=perms))
        return SecurePluginContext(
            sandbox=sandbox,
            storage="fake_storage",
            http_client="fake_http",
            config={"key": "val"},
        )

    def test_storage_returns_proxy_with_read_perm(self) -> None:
        ctx = self._ctx({Permission.STORAGE_READ})
        assert isinstance(ctx.storage, SecureStorageProxy)

    def test_storage_denied_without_permission(self) -> None:
        ctx = self._ctx(set())
        with pytest.raises(SecurityError):
            _ = ctx.storage

    def test_http_client_returns_proxy_with_network_perm(self) -> None:
        ctx = self._ctx({Permission.NETWORK_HTTP})
        assert isinstance(ctx.http_client, SecureHTTPClientProxy)

    def test_http_client_denied_without_permission(self) -> None:
        ctx = self._ctx(set())
        with pytest.raises(SecurityError):
            _ = ctx.http_client

    def test_config_returns_copy(self) -> None:
        ctx = self._ctx(set())
        cfg = ctx.config
        cfg["new_key"] = "new_val"
        assert ctx.config == {"key": "val"}

    def test_config_defaults_to_empty(self) -> None:
        sandbox = PluginSandbox(_manifest())
        ctx = SecurePluginContext(sandbox=sandbox)
        assert ctx.config == {}
