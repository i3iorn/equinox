"""JSON tree viewer widget with collapsible nodes.

Displays parsed JSON objects in a collapsible tree structure with:
- Expandable/collapsible nodes for objects and arrays
- Primitive value display
- Protection against extremely large responses (20k nodes max)
- Protection against deeply nested JSON (100 level max)
- Copy to clipboard functionality
- Responsive controls (Expand All, Collapse All, Copy JSON)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Hard limit on tree nodes to prevent Qt from hanging on huge responses.
_MAX_NODES = 20_000

# Hard limit on nesting depth to prevent Python RecursionError on deeply nested JSON
# (Python's default recursion limit is ~1000, we use 100 for safety margin)
_MAX_DEPTH = 100

# JSON serialization constants
_JSON_INDENT = 2
_ENSURE_ASCII = False

# UI constants
_BUTTON_WIDTH = 90
_TOOLBAR_SPACING = 4
_LAYOUT_MARGINS = (0, 2, 0, 0)


class _JsonTreeBuilder:
    """Internal builder for constructing JSON tree items.

    Separates tree building logic from UI rendering. Handles:
    - Recursive JSON traversal
    - Node limit enforcement
    - Depth limit enforcement
    - Safe JSON serialization
    """

    def __init__(self, max_nodes: int = _MAX_NODES, max_depth: int = _MAX_DEPTH) -> None:
        """Initialize builder with limits.

        Args:
            max_nodes: Maximum number of tree nodes to create
            max_depth: Maximum nesting depth
        """
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.node_count = 0
        self._root_item: QTreeWidgetItem | None = None

    def build(self, root_item: QTreeWidgetItem, obj: Any) -> None:
        """Build tree from JSON object.

        Args:
            root_item: Root item to populate
            obj: JSON object (from json.loads)

        Raises:
            TypeError: If obj is not JSON-serializable
        """
        self.node_count = 0
        self._root_item = root_item

        if isinstance(obj, (dict, list)):
            self._add_value(root_item, obj, depth=0)
        else:
            # Primitive at root level
            self._add_item(root_item, "value", self._serialize(obj))

    def _add_value(self, parent: QTreeWidgetItem, value: Any, depth: int) -> None:
        """Recursively add value to tree.

        Args:
            parent: Parent tree item
            value: Value to add (dict, list, or primitive)
            depth: Current depth level
        """
        # Depth check
        if depth > self.max_depth:
            self._add_item(parent, "…", "(max depth reached)")
            return

        # Node limit check
        if self.node_count >= self.max_nodes:
            self._add_item(parent, "…", "(truncated)")
            return

        if isinstance(value, dict):
            self._add_dict_items(parent, value, depth)
        elif isinstance(value, list):
            self._add_list_items(parent, value, depth)
        else:
            # Primitive value
            self._add_item(parent, "", self._serialize(value))

    def _add_dict_items(self, parent: QTreeWidgetItem, obj: dict, depth: int) -> None:
        """Add dictionary items to tree.

        Args:
            parent: Parent tree item
            obj: Dictionary to add
            depth: Current depth level
        """
        for key, val in obj.items():
            if self.node_count >= self.max_nodes:
                self._add_item(parent, "…", "(truncated)")
                return

            self.node_count += 1

            if isinstance(val, (dict, list)):
                # Create expandable node
                child = self._add_item(parent, str(key), "")
                self._add_value(child, val, depth + 1)
            else:
                # Add leaf node
                self._add_item(parent, str(key), self._serialize(val))

    def _add_list_items(self, parent: QTreeWidgetItem, arr: list, depth: int) -> None:
        """Add array items to tree.

        Args:
            parent: Parent tree item
            arr: Array to add
            depth: Current depth level
        """
        for idx, val in enumerate(arr):
            if self.node_count >= self.max_nodes:
                self._add_item(parent, "…", "(truncated)")
                return

            self.node_count += 1

            if isinstance(val, (dict, list)):
                # Create expandable node
                child = self._add_item(parent, f"[{idx}]", "")
                self._add_value(child, val, depth + 1)
            else:
                # Add leaf node
                self._add_item(parent, f"[{idx}]", self._serialize(val))

    @staticmethod
    def _add_item(parent: QTreeWidgetItem, key: str, value: str) -> QTreeWidgetItem:
        """Add a single tree item.

        Args:
            parent: Parent tree item
            key: Key or index
            value: Serialized value

        Returns:
            Newly created tree item
        """
        return QTreeWidgetItem(parent, [key, value])

    @staticmethod
    def _serialize(value: Any) -> str:
        """Serialize value to JSON string safely.

        Args:
            value: Value to serialize

        Returns:
            JSON string representation
        """
        try:
            return json.dumps(value, indent=0, ensure_ascii=_ENSURE_ASCII)
        except (TypeError, ValueError) as e:
            logger.debug("Failed to serialize value: %s", e)
            return str(value)


class JsonTree(QWidget):
    """Collapsible JSON viewer using QTreeWidget.

    Displays JSON objects and arrays as expandable nodes with primitive values
    as leaves. Provides controls for expanding/collapsing and copying JSON.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._last_obj: Any | None = None
        self._pending_obj: Any | None = None
        self._is_loaded = False
        self._builder = _JsonTreeBuilder(_MAX_NODES, _MAX_DEPTH)

        self._init_ui()

    def _init_ui(self) -> None:
        """Initialize UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_LAYOUT_MARGINS)
        layout.setSpacing(_TOOLBAR_SPACING)

        # Toolbar with controls
        self._setup_toolbar(layout)

        # Tree widget
        self.tree = self._create_tree()
        layout.addWidget(self.tree, 1)

        # Placeholder label
        self._placeholder = QLabel("(no JSON to display)")
        self._placeholder.setObjectName("mutedLabel")
        layout.addWidget(self._placeholder)

        self._show_placeholder(True)

    def _setup_toolbar(self, layout: QVBoxLayout) -> None:
        """Setup toolbar with action buttons.

        Args:
            layout: Parent layout to add toolbar to
        """
        toolbar = QHBoxLayout()

        # Buttons
        self._expand_btn = self._create_button("Expand All", self._on_expand_all)
        self._collapse_btn = self._create_button("Collapse All", self._on_collapse_all)
        self._copy_btn = self._create_button("Copy JSON", self._on_copy_json)

        toolbar.addWidget(self._expand_btn)
        toolbar.addWidget(self._collapse_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._copy_btn)

        layout.addLayout(toolbar)

    @staticmethod
    def _create_button(text: str, callback: Callable[[], None]) -> QPushButton:
        """Create a toolbar button.

        Args:
            text: Button label
            callback: Click callback

        Returns:
            Configured button
        """
        btn = QPushButton(text)
        btn.setMinimumWidth(_BUTTON_WIDTH)
        btn.clicked.connect(callback)
        return btn

    @staticmethod
    def _create_tree() -> QTreeWidget:
        """Create and configure tree widget.

        Returns:
            Configured QTreeWidget
        """
        tree = QTreeWidget()
        tree.setColumnCount(2)
        tree.setHeaderLabels(["Key", "Value"])

        # Configure columns
        hdr = tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        # Configure appearance
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)

        return tree

    def _show_placeholder(self, show: bool) -> None:
        """Show or hide placeholder label.

        Args:
            show: Whether to show placeholder
        """
        self._placeholder.setVisible(show)
        self.tree.setVisible(not show)

    def clear(self) -> None:
        """Clear the tree and show placeholder."""
        self._last_obj = None
        self._pending_obj = None
        self._is_loaded = False
        self._placeholder.setText("(no JSON to display)")
        self.tree.clear()
        self._show_placeholder(True)

    def load_json(self, obj: Any, defer: bool = False) -> None:
        """Load and display a JSON object.

        Safely loads a Python object (from json.loads) into the tree.
        Handles errors gracefully and logs failures for debugging.

        Args:
            obj: JSON object to display (dict, list, or primitive)

        Raises:
            TypeError: If obj cannot be JSON-serialized (logged, not raised to caller)
        """
        logger.debug("JsonTree.load_json: loading JSON top-level type=%s", type(obj).__name__)

        # Validate object is JSON-serializable
        if not self._is_json_serializable(obj):
            logger.warning("Object is not JSON-serializable: %s", type(obj).__name__)
            self.clear()
            return

        self._last_obj = obj
        self._pending_obj = obj

        if defer:
            self._is_loaded = False
            self.tree.clear()
            self._placeholder.setText("(JSON ready - open tab to render)")
            self._show_placeholder(True)
            return

        self.ensure_loaded()

    def ensure_loaded(self) -> None:
        """Build the tree if it has a pending JSON object and is not loaded yet."""
        if self._is_loaded:
            return

        obj = self._pending_obj
        if obj is None:
            self._show_placeholder(True)
            return

        self.tree.clear()
        root = self.tree.invisibleRootItem()

        try:
            if root is None:
                logger.error("Failed to get tree root item")
                self._handle_load_error()
                return
            self._builder.build(root, obj)
            self.tree.expandToDepth(0)
            self._show_placeholder(False)
            self._placeholder.setText("(no JSON to display)")
            self._is_loaded = True
        except RecursionError:
            logger.exception("RecursionError while building tree (JSON too deeply nested)")
            self._handle_load_error()
        except Exception:
            logger.exception("Failed to load JSON into tree")
            self._handle_load_error()

    @staticmethod
    def _is_json_serializable(obj: Any) -> bool:
        """Check if object is JSON-serializable.

        Args:
            obj: Object to check

        Returns:
            True if serializable, False otherwise
        """
        try:
            json.dumps(obj, default=str)
            return True
        except (TypeError, ValueError):
            return False

    def _handle_load_error(self) -> None:
        """Handle tree loading errors gracefully."""
        try:
            self.tree.clear()
        except Exception:
            pass
        self._is_loaded = False
        self._placeholder.setText("(no JSON to display)")
        self._show_placeholder(True)

    def _on_expand_all(self) -> None:
        """Expand all tree nodes."""
        self.ensure_loaded()
        self.tree.expandAll()

    def _on_collapse_all(self) -> None:
        """Collapse all tree nodes."""
        self.ensure_loaded()
        self.tree.collapseAll()

    def _on_copy_json(self) -> None:
        """Copy current JSON to clipboard."""
        if self._last_obj is None:
            return

        try:
            text = json.dumps(
                self._last_obj,
                indent=_JSON_INDENT,
                ensure_ascii=_ENSURE_ASCII,
            )
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
                logger.debug("JSON copied to clipboard")
        except Exception:
            logger.exception("Failed to copy JSON to clipboard")
