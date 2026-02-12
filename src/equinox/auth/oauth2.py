"""OAuth2 authentication"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import httpx

from equinox.auth.base import AuthStrategy
from equinox.core.exceptions import AuthError


class OAuth2Auth(AuthStrategy):
    """OAuth2 authentication with token management"""

    def __init__(
        self,
        access_token: Optional[str] = None,
        token_url: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        scope: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ):
        """
        Initialize OAuth2 auth

        Args:
            access_token: Current access token
            token_url: URL to obtain tokens
            client_id: OAuth2 client ID
            client_secret: OAuth2 client secret
            scope: OAuth2 scope
            refresh_token: Refresh token for obtaining new access tokens
        """
        self.access_token = access_token
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.refresh_token = refresh_token
        self.expires_at: Optional[datetime] = None

    def apply(self, request: Any, headers: Dict[str, str]) -> None:
        """Add Authorization header with OAuth2 token"""
        # Check if token needs refresh
        if self._needs_refresh():
            self._refresh_access_token()

        if not self.access_token:
            raise AuthError("No access token available")

        headers["Authorization"] = f"Bearer {self.access_token}"

    def _needs_refresh(self) -> bool:
        """Check if token needs refreshing"""
        if not self.access_token:
            return True
        if self.expires_at and datetime.now() >= self.expires_at:
            return True
        return False

    def _refresh_access_token(self) -> None:
        """Refresh the access token using client credentials or refresh token"""
        if not self.token_url:
            raise AuthError("No token URL configured")

        try:
            data = {}
            if self.refresh_token:
                # Use refresh token
                data = {
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                }
            elif self.client_id and self.client_secret:
                # Use client credentials
                data = {
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                }
                if self.scope:
                    data["scope"] = self.scope

            response = httpx.post(self.token_url, data=data)
            response.raise_for_status()

            token_data = response.json()
            self.access_token = token_data["access_token"]

            # Set expiration time
            if "expires_in" in token_data:
                expires_in = int(token_data["expires_in"])
                self.expires_at = datetime.now() + timedelta(seconds=expires_in)

            # Update refresh token if provided
            if "refresh_token" in token_data:
                self.refresh_token = token_data["refresh_token"]

        except httpx.HTTPError as e:
            raise AuthError(f"Failed to refresh token: {e}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "type": "oauth2",
            "access_token": self.access_token,
            "token_url": self.token_url,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": self.scope,
            "refresh_token": self.refresh_token,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OAuth2Auth":
        """Create from dictionary"""
        return cls(
            access_token=data.get("access_token"),
            token_url=data.get("token_url"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scope=data.get("scope"),
            refresh_token=data.get("refresh_token"),
        )

    def __repr__(self) -> str:
        token_preview = f"{self.access_token[:8]}..." if self.access_token and len(self.access_token) > 8 else "None"
        return f"OAuth2Auth(access_token={token_preview})"
