from __future__ import annotations

import logging
from typing import Any
from typing import Protocol


class _GroupServiceManagerProtocol(Protocol):
    """Minimal manager contract used by GroupService."""

    def list_groups(self) -> list[dict[str, Any]]: ...
    def create_group(self, name: str, description: str) -> None: ...
    def delete_group(self, group_id: int) -> None: ...
    def update_group(self, group_id: int, name: str) -> None: ...
    def list_group_variables(self, group_id: int) -> list[dict[str, Any]]: ...
    def add_variable(self, group_id: int, key: str, value: str, description: str) -> None: ...
    def remove_variable(self, group_id: int, key: str) -> None: ...


class GroupService:
    """Business logic layer for variable groups and variables."""

    def __init__(self, mgr: _GroupServiceManagerProtocol, logger: logging.Logger) -> None:
        self._mgr = mgr
        self._log = logger

    # ── Groups ──────────────────────────────────────────────────────────────

    def list_groups(self) -> list[dict[str, Any]]:
        return self._mgr.list_groups()

    def create_group(self, name: str, description: str) -> None:
        self._mgr.create_group(name, description)

    def delete_group(self, group_id: int) -> None:
        self._mgr.delete_group(group_id)

    def rename_group(self, group_id: int, new_name: str) -> None:
        self._mgr.update_group(group_id, name=new_name)

    # ── Variables ───────────────────────────────────────────────────────────

    def list_variables(self, group_id: int) -> list[dict[str, Any]]:
        return self._mgr.list_group_variables(group_id)

    def add_variable(self, group_id: int, key: str, value: str, desc: str) -> None:
        self._mgr.add_variable(group_id, key, value, desc)

    def update_variable(
        self,
        group_id: int,
        old_key: str,
        new_key: str,
        value: str,
        desc: str,
    ) -> None:
        if new_key != old_key:
            self._mgr.remove_variable(group_id, old_key)
        self._mgr.add_variable(group_id, new_key, value, desc)

    def remove_variable(self, group_id: int, key: str) -> None:
        self._mgr.remove_variable(group_id, key)
