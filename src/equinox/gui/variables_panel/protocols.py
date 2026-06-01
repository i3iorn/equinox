from __future__ import annotations

from collections.abc import Callable
from typing import Any
from typing import Protocol


class GroupRecord(Protocol):
    id: int
    name: str
    description: str | None


class VariableRecord(Protocol):
    id: int
    key: str
    value: str
    description: str | None


class GroupManagerProtocol(Protocol):
    """Protocol describing the variable-group manager API."""

    def list_groups(self) -> list[dict[str, Any]]: ...
    def create_group(self, name: str, description: str) -> None: ...
    def delete_group(self, group_id: int) -> None: ...
    def update_group(self, group_id: int, name: str) -> None: ...

    def list_group_variables(self, group_id: int) -> list[dict[str, Any]]: ...
    def add_variable(self, group_id: int, key: str, value: str, description: str) -> None: ...
    def remove_variable(self, group_id: int, key: str) -> None: ...


class SettingsProtocol(Protocol):
    """Protocol for QSettings-like persistence."""

    def value(self, key: str, default: Any = None) -> Any: ...
    def setValue(self, key: str, value: Any) -> None: ...


class OrderedContextAction(Protocol):
    """Protocol for context menu action definitions."""

    action_id: str
    label: str
    callback: Callable[[], None]
    destructive: bool
