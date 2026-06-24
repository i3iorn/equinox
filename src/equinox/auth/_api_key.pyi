from __future__ import annotations
from typing import Any, Dict, Literal, Optional
from equinox.auth._base import AuthStrategy

class APIKeyAuth(AuthStrategy):
    AUTH_TYPE: str
    DISPLAY_NAME: str
    key: str
    value: str
    location: Literal["header", "query"]

    def __init__(
        self,
        key: str,
        value: str,
        location: Literal["header", "query"] = "header",
    ) -> None: ...

    def apply(self, request: Any, headers: Dict[str, str]) -> None: ...

    def to_dict(self) -> Dict[str, Any]: ...

    @classmethod
    def from_dict(cls, data: Dict[str, Any], **kwargs: Any) -> APIKeyAuth: ...

    def get_display_summary(self) -> str: ...

    def get_preflight_warning(self) -> Optional[str]: ...

    def __eq__(self, other: object) -> bool: ...
    def __hash__(self) -> int: ...
    def __repr__(self) -> str: ...
