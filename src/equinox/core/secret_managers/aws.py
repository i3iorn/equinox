"""AWS Secrets Manager backend.

Retrieves secrets from AWS Secrets Manager using boto3.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from equinox.core.secret_managers.base import (
    SecretManager,
    SecretManagerError,
    SecretNotFoundError,
    SecretAuthError,
)
from equinox.core.redact import mask_secret

logger = logging.getLogger(__name__)


class AWSSecretsManagerBackend(SecretManager):
    """AWS Secrets Manager backend for storing and retrieving secrets.

    Requires boto3 and AWS credentials (via IAM role, environment variables, or config file).
    Supports both string and JSON secrets.

    Example:
        mgr = AWSSecretsManagerBackend()
        mgr.configure(region_name="us-east-1")
        secret = mgr.get_secret("my-app/db-password")
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize AWS Secrets Manager backend."""
        super().__init__(**kwargs)
        self.client = None
        self.region_name: Optional[str] = None

    def configure(self, region_name: str = "us-east-1", **kwargs: Any) -> None:
        """Configure AWS Secrets Manager connection.

        Args:
            region_name: AWS region (default: us-east-1)
            **kwargs: Additional boto3 client parameters

        Raises:
            SecretAuthError: If AWS credentials are not available
            SecretManagerError: If boto3 is not installed
        """
        try:
            import boto3
        except ImportError:
            raise SecretManagerError(
                "boto3 is required for AWS Secrets Manager. "
                "Install with: pip install boto3"
            )

        try:
            self.region_name = region_name
            self.client = boto3.client("secretsmanager", region_name=region_name, **kwargs)
            # Test connectivity by listing secrets (with limit=1)
            self.client.list_secrets(MaxResults=1)
            self._configured = True
            logger.info("AWS Secrets Manager configured (region: %s)", region_name)
        except Exception as exc:
            raise SecretAuthError(
                f"Failed to configure AWS Secrets Manager: {exc}"
            )

    def get_secret(self, secret_name: str) -> str:
        """Retrieve a secret from AWS Secrets Manager.

        Args:
            secret_name: Name or ARN of the secret

        Returns:
            The secret value

        Raises:
            SecretNotFoundError: If the secret does not exist
            SecretManagerError: If retrieval fails
        """
        if not self._configured:
            raise SecretManagerError("AWS Secrets Manager not configured")

        # Check cache
        cached = self._get_from_cache(secret_name)
        if cached is not None:
            return cached

        secret_ref = mask_secret(secret_name, keep=4)

        try:
            response = self.client.get_secret_value(SecretId=secret_name)

            # AWS returns either SecretString or SecretBinary
            if "SecretString" in response:
                value = response["SecretString"]
            elif "SecretBinary" in response:
                value = response["SecretBinary"].decode("utf-8")
            else:
                raise SecretManagerError(f"Invalid secret response for {secret_ref}")

            self._validate_secret_length(value, secret_name)
            self._store_in_cache(secret_name, value)
            logger.debug("Retrieved secret from AWS Secrets Manager: %s", secret_ref)
            return value

        except self.client.exceptions.ResourceNotFoundException:
            raise SecretNotFoundError(f"Secret not found in AWS: {secret_ref}")
        except self.client.exceptions.AccessDeniedException as exc:
            raise SecretAuthError(f"Access denied to AWS secret: {secret_ref}") from exc
        except Exception as exc:
            raise SecretManagerError(
                f"Failed to retrieve secret from AWS: {exc}"
            )

    def get_secret_dict(self, secret_name: str) -> Dict[str, Any]:
        """Retrieve a secret from AWS Secrets Manager as JSON.

        Args:
            secret_name: Name or ARN of the secret

        Returns:
            Parsed JSON dictionary

        Raises:
            SecretNotFoundError: If the secret does not exist
            SecretManagerError: If retrieval fails or value is not valid JSON
        """
        value = self.get_secret(secret_name)
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise SecretManagerError(
                f"Secret '{mask_secret(secret_name, keep=4)}' is not valid JSON: {exc}"
            )

    def is_available(self) -> bool:
        """Check if AWS Secrets Manager is available and configured."""
        return self._configured and self.client is not None

