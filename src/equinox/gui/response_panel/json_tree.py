import json
import logging

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget, QHeaderView, QLabel, \
    QTreeWidgetItem, QApplication

logger = logging.getLogger(__name__)

# Hard limit on tree nodes to prevent Qt from hanging on huge responses.
_MAX_NODES = 20_000
# Hard limit on nesting depth to prevent Python RecursionError on deeply
# nested JSON (Python's default recursion limit is ~1 000).
_MAX_DEPTH = 100


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
        logger.debug("JsonTree.load_json: loading JSON top-level type=%s", type(obj))
        self._last_obj = obj
        self._node_count = 0
        self.tree.clear()
        root = self.tree.invisibleRootItem()

        def _add(parent_item, value, depth: int) -> None:
            if depth > _MAX_DEPTH:
                QTreeWidgetItem(parent_item, ["…", "(max depth reached)"])
                return
            if self._node_count >= _MAX_NODES:
                QTreeWidgetItem(parent_item, ["…", "(truncated)"])
                return

            if isinstance(value, dict):
                for k, v in value.items():
                    if self._node_count >= _MAX_NODES:
                        QTreeWidgetItem(parent_item, ["…", "(truncated)"])
                        return
                    self._node_count += 1
                    if isinstance(v, (dict, list)):
                        child = QTreeWidgetItem(parent_item, [str(k), ""])
                        _add(child, v, depth + 1)
                    else:
                        QTreeWidgetItem(parent_item, [str(k), json.dumps(v, ensure_ascii=False)])

            elif isinstance(value, list):
                for i, v in enumerate(value):
                    if self._node_count >= _MAX_NODES:
                        QTreeWidgetItem(parent_item, ["…", "(truncated)"])
                        return
                    self._node_count += 1
                    if isinstance(v, (dict, list)):
                        child = QTreeWidgetItem(parent_item, [f"[{i}]", ""])
                        _add(child, v, depth + 1)
                    else:
                        QTreeWidgetItem(parent_item, [f"[{i}]", json.dumps(v, ensure_ascii=False)])

            else:
                # Primitive at the root level
                QTreeWidgetItem(parent_item, ["", json.dumps(value, ensure_ascii=False)])

        try:
            if isinstance(obj, (dict, list)):
                _add(root, obj, depth=0)
            else:
                QTreeWidgetItem(root, ["value", json.dumps(obj, ensure_ascii=False)])

            self.tree.expandToDepth(0)
            self._show_placeholder(False)
        except Exception:
            logger.exception("JsonTree.load_json: failed while populating tree")
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
