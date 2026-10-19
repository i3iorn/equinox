"""Tests for SecureStorageProxy and SecureHTTPClientProxy permission enforcement."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from equinox.core.exceptions import SecurityError
from equinox.plugins.security import (
    Permission,
    PluginManifest,
    PluginSandbox,
    ResourceLimits,
    SecureHTTPClientProxy,
    SecureStorageProxy,
)


def _sandbox(perms: set[Permission], max_net: int = 100, max_store: int = 100) -> PluginSandbox:
    return PluginSandbox(
        PluginManifest(name="proxy-test", version="1.0", author="t", permissions=perms),
        limits=ResourceLimits(max_network_requests=max_net, max_storage_operations=max_store),
    )


# ── SecureStorageProxy ───────────────────────────────────────────────────────


class TestSecureStorageProxy:
    def test_fetchone_passes_with_read_perm(self) -> None:
        sb = _sandbox({Permission.STORAGE_READ})
        backend = MagicMock(spec=["fetchone"])
        backend.fetchone.return_value = ("row",)
        proxy = SecureStorageProxy(sb, backend)
        assert proxy.fetchone("SELECT 1") == ("row",)
        backend.fetchone.assert_called_once_with("SELECT 1", ())

    def test_fetchone_denied_without_read_perm(self) -> None:
        sb = _sandbox(set())
        proxy = SecureStorageProxy(sb, MagicMock())
        with pytest.raises(SecurityError, match="missing permission"):
            proxy.fetchone("SELECT 1")

    def test_fetchall_passes_with_read_perm(self) -> None:
        sb = _sandbox({Permission.STORAGE_READ})
        backend = MagicMock(spec=["fetchall"])
        backend.fetchall.return_value = [("a",), ("b",)]
        proxy = SecureStorageProxy(sb, backend)
        result = proxy.fetchall("SELECT *")
        assert len(result) == 2

    def test_execute_passes_with_write_perm(self) -> None:
        sb = _sandbox({Permission.STORAGE_WRITE})
        backend = MagicMock(spec=["execute"])
        backend.execute.return_value = None
        proxy = SecureStorageProxy(sb, backend)
        proxy.execute("INSERT INTO t VALUES (1)")
        backend.execute.assert_called_once()

    def test_execute_denied_without_write_perm(self) -> None:
        sb = _sandbox({Permission.STORAGE_READ})  # read only
        proxy = SecureStorageProxy(sb, MagicMock())
        with pytest.raises(SecurityError, match="missing permission"):
            proxy.execute("DELETE FROM t")

    def test_storage_operation_counter_increments(self) -> None:
        sb = _sandbox({Permission.STORAGE_READ}, max_store=2)
        backend = MagicMock(spec=["fetchone"])
        backend.fetchone.return_value = None
        proxy = SecureStorageProxy(sb, backend)
        proxy.fetchone("q")
        proxy.fetchone("q")
        with pytest.raises(SecurityError, match="storage operation limit"):
            proxy.fetchone("q")


# ── SecureHTTPClientProxy ────────────────────────────────────────────────────


class TestSecureHTTPClientProxy:
    def test_send_passes_with_network_perm(self) -> None:
        sb = _sandbox({Permission.NETWORK_HTTP})
        backend = MagicMock(spec=["send"])
        backend.send.return_value = "resp"
        proxy = SecureHTTPClientProxy(sb, backend)
        assert proxy.send("req") == "resp"

    def test_send_denied_without_network_perm(self) -> None:
        sb = _sandbox(set())
        proxy = SecureHTTPClientProxy(sb, MagicMock())
        with pytest.raises(SecurityError, match="missing permission"):
            proxy.send("req")

    def test_get_passes_with_network_perm(self) -> None:
        sb = _sandbox({Permission.NETWORK_HTTP})
        backend = MagicMock(spec=["get"])
        backend.get.return_value = "resp"
        proxy = SecureHTTPClientProxy(sb, backend)
        assert proxy.get("https://example.com") == "resp"
        backend.get.assert_called_once_with("https://example.com")

    def test_post_passes_with_network_perm(self) -> None:
        sb = _sandbox({Permission.NETWORK_HTTP})
        backend = MagicMock(spec=["post"])
        proxy = SecureHTTPClientProxy(sb, backend)
        proxy.post("https://example.com", json={"a": 1})
        backend.post.assert_called_once_with("https://example.com", json={"a": 1})

    def test_post_denied_without_network_perm(self) -> None:
        sb = _sandbox(set())
        proxy = SecureHTTPClientProxy(sb, MagicMock())
        with pytest.raises(SecurityError, match="missing permission"):
            proxy.post("https://example.com")

    def test_network_request_counter_enforces_limit(self) -> None:
        sb = _sandbox({Permission.NETWORK_HTTP}, max_net=2)
        backend = MagicMock(spec=["get"])
        proxy = SecureHTTPClientProxy(sb, backend)
        proxy.get("url1")
        proxy.get("url2")
        with pytest.raises(SecurityError, match="network request limit"):
            proxy.get("url3")
