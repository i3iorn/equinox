from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from equinox.core.secret_managers.base import SecretManagerError
from equinox.core.secret_managers.vault import VaultManager


@patch("requests.get")
def test_vault_config_rejects_invalid_scheme_without_network(mock_get: Mock) -> None:
    mgr = VaultManager()

    with pytest.raises(SecretManagerError, match="Invalid Vault URL"):
        mgr.configure(url="ftp://vault.example.com:8200", token="token")

    mock_get.assert_not_called()


@patch("requests.get")
def test_vault_config_rejects_private_host_without_network(mock_get: Mock) -> None:
    mgr = VaultManager()

    with pytest.raises(SecretManagerError, match="Invalid Vault URL"):
        mgr.configure(url="https://127.0.0.1:8200", token="token")

    mock_get.assert_not_called()


@patch("requests.get")
def test_vault_config_denies_plain_http_by_default(mock_get: Mock) -> None:
    mgr = VaultManager()

    with pytest.raises(SecretManagerError, match="must use https"):
        mgr.configure(url="http://vault.example.com:8200", token="token")

    mock_get.assert_not_called()


@patch("requests.get")
def test_vault_config_allows_plain_http_when_explicitly_enabled(mock_get: Mock) -> None:
    mock_get.return_value = Mock(status_code=200)
    mgr = VaultManager()

    mgr.configure(
        url="http://vault.example.com:8200",
        token="token",
        allow_insecure_http=True,
    )

    assert mgr.is_available()
    assert mgr.url == "http://vault.example.com:8200"
    mock_get.assert_called_once()

