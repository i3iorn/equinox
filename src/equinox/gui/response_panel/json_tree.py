import json

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget, QHeaderView, QLabel, \
    QTreeWidgetItem, QApplication


class JsonTree(QWidget):
    """Simple collapsible JSON viewer using QTreeWidget.

    Shows objects and arrays as expandable nodes and primitive values as leaves.
    Provides Expand All / Collapse All / Copy JSON controls.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_obj = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(4)

        # Toolbar
        toolbar = QHBoxLayout()
        self._expand_btn = QPushButton("Expand All")
        self._expand_btn.setFixedWidth(90)
        self._collapse_btn = QPushButton("Collapse All")
        self._collapse_btn.setFixedWidth(90)
        self._copy_btn = QPushButton("Copy JSON")
        self._copy_btn.setFixedWidth(90)

        self._expand_btn.clicked.connect(self._on_expand_all)
        self._collapse_btn.clicked.connect(self._on_collapse_all)
        self._copy_btn.clicked.connect(self._on_copy_json)

        toolbar.addWidget(self._expand_btn)
        toolbar.addWidget(self._collapse_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._copy_btn)
        layout.addLayout(toolbar)

        # Tree
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Key", "Value"])
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        layout.addWidget(self.tree, 1)

        # Placeholder label when no JSON is loaded
        self._placeholder = QLabel("(no JSON to display)")
        self._placeholder.setObjectName("mutedLabel")
        layout.addWidget(self._placeholder)

        self._show_placeholder(True)

    def _show_placeholder(self, show: bool) -> None:
        self._placeholder.setVisible(show)
        self.tree.setVisible(not show)

    def clear(self) -> None:
        self._last_obj = None
        self.tree.clear()
        self._show_placeholder(True)

    def load_json(self, obj) -> None:
        """Load a Python object (from json.loads) into the tree."""
        import logging
        logger = logging.getLogger(__name__)
        logger.debug("JsonTree.load_json: loading JSON top-level type=%s", type(obj))
        self._last_obj = obj
        # guard: avoid creating excessive QTreeWidgetItems which may crash Qt
        self._node_count = 0
        MAX_NODES = 20000
        self.tree.clear()
        root = self.tree.invisibleRootItem()

        def _add(parent_item, value):
            # stop early if we've created too many nodes
            if getattr(self, "_node_count", 0) >= MAX_NODES:
                QTreeWidgetItem(parent_item, ["...", "(truncated)"])
                return
            # incremental node count
            # Note: we count items before recursing to avoid deep recursion explosion
            if isinstance(value, dict):
                for k, v in value.items():
                    if getattr(self, "_node_count", 0) >= MAX_NODES:
                        QTreeWidgetItem(parent_item, ["...", "(truncated)"])
                        return
                    self._node_count += 1
                    if isinstance(v, (dict, list)):
                        child = QTreeWidgetItem(parent_item, [str(k), ""])
                        _add(child, v)
                    else:
                        # Show primitives as JSON-encoded strings for fidelity
                        child = QTreeWidgetItem(parent_item, [str(k), json.dumps(v, ensure_ascii=False)])
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    if getattr(self, "_node_count", 0) >= MAX_NODES:
                        QTreeWidgetItem(parent_item, ["...", "(truncated)"])
                        return
                    key = f"[{i}]"
                    self._node_count += 1
                    if isinstance(v, (dict, list)):
                        child = QTreeWidgetItem(parent_item, [key, ""])
                        _add(child, v)
                    else:
                        child = QTreeWidgetItem(parent_item, [key, json.dumps(v, ensure_ascii=False)])
            else:
                # Fallback for primitives at the root
                QTreeWidgetItem(parent_item, ["", json.dumps(value, ensure_ascii=False)])

        try:
            # If the top-level object is a primitive, create one root item
            if isinstance(obj, (dict, list)):
                _add(root, obj)
            else:
                QTreeWidgetItem(root, ["value", json.dumps(obj, ensure_ascii=False)])

            self.tree.expandToDepth(0)
            self._show_placeholder(False)
        except Exception:
            logger.exception("JsonTree.load_json: failed while populating tree")
            # clear partially-constructed tree and show placeholder
            try:
                self.tree.clear()
            except Exception:
                pass
            self._show_placeholder(True)

    def _on_expand_all(self) -> None:
        self.tree.expandAll()

    def _on_collapse_all(self) -> None:
        self.tree.collapseAll()

    def _on_copy_json(self) -> None:
        if self._last_obj is None:
            return
        try:
            text = json.dumps(self._last_obj, indent=2, ensure_ascii=False)
            QApplication.clipboard().setText(text)
        except Exception:
            pass
