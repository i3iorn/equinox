"""Generic list+form dialog control flow mixin.

This mixin extracts the common patterns for dialogs with a left list and right form editor:

- List refresh with blockSignals and custom item rendering
- Selection change handling with dirty-state prompting
- Form enable/disable and button synchronization
- Dirty-tracking coordination

Subclasses inherit from both this mixin and DirtyDialogMixin, and must define the
template methods:
- _build_list_items() → list of (item_id, label) for rendering
- _on_list_item_selected(item_id) → load form from item_id
- _set_form_enabled(enabled) → enable/disable form widgets
- _sync_buttons() → update button states based on current selection
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Union

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidget, QListWidgetItem
from typing import TypeAlias

from equinox.gui.dialogs._dirty_dialog_mixin import DirtyDialogMixin

_ListItemSpec: TypeAlias = Union[tuple[int, str], tuple[int, str, dict[str, Any]]]


class ListFormDialogMixin(DirtyDialogMixin):
    """Mixin providing standard list+form control flow for manager dialogs.

    **Required attributes** (set by the subclass before calling helpers):

    ``_list_widget``
        The ``QListWidget`` on the left holding items.

    ``_current_id``
        The currently-selected item ID (or None). Updated automatically.

    ``_dirty``
        Whether the form has unsaved changes (set via ``_mark_dirty()``).

    **Template methods** (implement in subclass):

    ``_build_list_items()`` → list of (item_id, label, **kwargs)
        Yield or return tuples of (item_id, label) for each list item.
        Optional: pass extra kwargs to customize item rendering (fg_color, font, etc).

    ``_on_list_item_selected(item_id: int)`` → None
        Load the form from the given item_id. Also used to update the form header.

    ``_set_form_enabled(enabled: bool)`` → None
        Enable or disable all form widgets and action buttons.

    ``_sync_buttons()`` → None
        Update button states based on ``_current_id`` and ``_dirty``.
    """

    _list_widget: QListWidget
    _current_id: int | None
    _dirty: bool

    # ── Standard refresh+selection pattern ─────────────────────────────

    def _refresh_list(self, select_id: int | None = None) -> None:
        """Rebuild the list from ``_build_list_items()`` and restore selection.

        If ``select_id`` is given, that item is pre-selected while signals are
        blocked. Otherwise, the first item fires the normal selection signal.
        Then ``_apply_selection()`` is called to load the form (if needed).
        """
        self._list_widget.setUpdatesEnabled(False)
        self._list_widget.blockSignals(True)
        try:
            self._list_widget.clear()
            for item_id, label, *extras in self._build_list_items():
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, item_id)
                # Apply optional extra kwargs (fg_color, font, etc)
                if extras:
                    kwargs = extras[0] if isinstance(extras[0], dict) else {}
                    if "fg_color" in kwargs:
                        from PyQt6.QtGui import QColor

                        item.setForeground(QColor(kwargs["fg_color"]))
                    if "font" in kwargs:
                        item.setFont(kwargs["font"])
                self._list_widget.addItem(item)
                if item_id == select_id:
                    self._list_widget.setCurrentItem(item)
        finally:
            self._list_widget.blockSignals(False)
            self._list_widget.setUpdatesEnabled(True)

        if select_id is None and self._list_widget.count():
            # Trigger currentItemChanged → _on_item_selected
            self._list_widget.setCurrentRow(0)
        else:
            # Item was pre-selected while signals blocked — manually drive selection
            self._apply_selection()

    # ── Selection handlers ────────────────────────────────────────────

    def _apply_selection(self) -> None:
        """Load the currently-selected list item into the form.

        Does NOT prompt about unsaved changes — only called after a
        programmatic list rebuild (create/delete/save).
        """
        current = self._list_widget.currentItem()
        if current is None:
            self._current_id = None
            self._set_form_enabled(False)
            self._sync_buttons()  # Allow subclass to disable list action buttons
            return

        item_id = current.data(Qt.ItemDataRole.UserRole)
        self._current_id = item_id
        if item_id is not None:
            self._on_list_item_selected(int(item_id))
        self._set_form_enabled(True)
        self._dirty = False
        self._sync_buttons()

    def _on_item_selected(
        self,
        current: QListWidgetItem | None,
        _prev: QListWidgetItem | None,
    ) -> None:
        """Handle interactive selection changes from the list (signal slot).

        Checks if form is dirty and prompts to save before switching.
        """
        if current is None:
            self._current_id = None
            self._set_form_enabled(False)
            return

        new_id = current.data(Qt.ItemDataRole.UserRole)
        if new_id == self._current_id:
            return  # same item re-selected — no-op

        if self._dirty and self._current_id is not None:
            if not self._prompt_unsaved(self._current_id):
                return

        self._current_id = new_id
        if new_id is not None:
            self._on_list_item_selected(int(new_id))
        self._set_form_enabled(True)
        self._dirty = False
        self._sync_buttons()

    # ── Dirty tracking ────────────────────────────────────────────────

    def _mark_dirty(self) -> None:
        """Mark the form as having unsaved changes."""
        if not self._dirty:
            self._dirty = True
            self._sync_buttons()

    # ── Abstract/template methods (override in subclass) ──────────────

    def _build_list_items(self) -> Iterable[_ListItemSpec]:
        """Yield (item_id, label, **kwargs) tuples for each list item.

        Override to customize item rendering. Optional kwargs:
        - fg_color: color string for item text
        - font: QFont for item text

        Example:
            for client in self.mgr.list_clients():
                yield (
                    client["id"],
                    f"{client['name']}  [{client['grant_type']}]",
                    {"fg_color": Colors.BLUE if client["is_default"] else None}
                )
        """
        raise NotImplementedError("Subclass must implement _build_list_items()")

    def _on_list_item_selected(self, item_id: int) -> None:
        """Load the form for the given item_id."""
        raise NotImplementedError("Subclass must implement _on_list_item_selected()")

    def _set_form_enabled(self, enabled: bool) -> None:
        """Enable or disable all form widgets."""
        raise NotImplementedError("Subclass must implement _set_form_enabled()")

    def _sync_buttons(self) -> None:
        """Update button states based on current selection and dirty flag."""
        raise NotImplementedError("Subclass must implement _sync_buttons()")
