"""Import/export operations mixin for MainWindow."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QProgressDialog,
)

from equinox.storage import CollectionManager

logger = logging.getLogger(__name__)


class _ImportExportMixin:
    """Background import/export operations with progress dialog and retry."""

    def _import_with(
        self,
        importer_class: type,
        dialog_title: str,
        file_filter: str,
        success_msg: str,
    ) -> None:
        """Generic import handler with background execution and retry."""
        file_path, _ = QFileDialog.getOpenFileName(self, dialog_title, "", file_filter)
        if not file_path:
            return
        self._start_import(importer_class, Path(file_path), success_msg)

    def _start_import(self, importer_class: type, file_path: Path, success_msg: str) -> None:
        """Run selected importer in background with retry on error."""

        def _operation(cancel_event: object | None = None) -> bool:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Import cancelled")
            mgr = CollectionManager(self.db)
            importer = importer_class(mgr)
            importer.import_file(file_path)
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Import cancelled")
            return True

        self._run_background_task(
            operation=_operation,
            operation_name=f"Importing {file_path.name}...",
            success_msg=success_msg,
            error_title="Import Error",
            on_success=lambda _result: self._refresh_collections_after_background(),
            retry_operation=lambda: self._start_import(importer_class, file_path, success_msg),
        )

    def _refresh_collections_after_background(self) -> None:
        """Refresh collections panel now, or queue refresh until tab is opened."""
        if self.collections_panel is not None:
            self._safe_refresh(self.collections_panel)
            return
        self._pending_panel_refreshes.add(0)

    def _run_background_task(
        self,
        operation,
        operation_name: str,
        success_msg: str,
        error_title: str,
        on_success=None,
        retry_operation=None,
    ) -> None:
        """Execute a blocking operation on a worker thread with progress UX."""
        from equinox.gui.workers import BackgroundTaskWorker

        progress = QProgressDialog(operation_name, "Cancel", 0, 0, self)
        progress.setWindowTitle("Working")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        worker = BackgroundTaskWorker(operation, parent=self)
        self._background_workers.add(worker)

        def _cleanup() -> None:
            try:
                progress.close()
            except Exception:
                pass
            self._background_workers.discard(worker)
            worker.deleteLater()

        def _on_cancel() -> None:
            worker.cancel()
            progress.setLabelText("Cancelling...")
            progress.setCancelButton(None)

        def _on_finished(success: bool, payload: object) -> None:
            _cleanup()
            if success:
                self.status_bar.showMessage(success_msg, 4000)
                if callable(on_success):
                    on_success(payload)
                return

            error_text = str(payload)
            retry_btn = QMessageBox.StandardButton.Retry
            cancel_btn = QMessageBox.StandardButton.Cancel
            choice = QMessageBox.question(
                self,
                error_title,
                f"{error_text}\n\nRetry the operation?",
                retry_btn | cancel_btn,
                retry_btn,
            )
            if choice == retry_btn and callable(retry_operation):
                retry_operation()

        progress.canceled.connect(_on_cancel)
        worker.finished.connect(_on_finished)
        worker.start()

    def _import_postman(self) -> None:
        from equinox.importers import PostmanImporter

        self._import_with(
            PostmanImporter,
            "Import Postman Collection",
            "JSON Files (*.json);;All Files (*)",
            "Postman collection imported",
        )

    def _import_openapi(self) -> None:
        from equinox.importers import OpenAPIImporter

        self._import_with(
            OpenAPIImporter,
            "Import OpenAPI/Swagger Specification",
            "API Spec Files (*.json *.yaml *.yml);;All Files (*)",
            "OpenAPI specification imported",
        )

    def _import_har(self) -> None:
        from equinox.importers import HARImporter

        self._import_with(
            HARImporter,
            "Import HAR File",
            "HAR Files (*.har);;JSON Files (*.json);;All Files (*)",
            "HAR file imported",
        )

    def _import_insomnia(self) -> None:
        from equinox.importers import InsomniaImporter

        self._import_with(
            InsomniaImporter,
            "Import Insomnia Collection",
            "JSON Files (*.json);;All Files (*)",
            "Insomnia collection imported",
        )

    def _export_collection(self, format_type: str) -> None:
        mgr = CollectionManager(self.db)
        collections = mgr.list_collections()
        if not collections:
            QMessageBox.warning(self, "No Collections", "No collections to export.")
            return

        col_names = [col["name"] for col in collections]
        col_name, ok = QInputDialog.getItem(
            self,
            "Select Collection",
            "Choose collection to export:",
            col_names,
            0,
            False,
        )
        if not ok or not isinstance(col_name, str) or not col_name:
            return

        collection_id = next((c["id"] for c in collections if c["name"] == col_name), None)
        if collection_id is None:
            QMessageBox.warning(self, "Export Error", f"Collection '{col_name}' not found.")
            return
        collection_id = int(collection_id)

        file_filter = (
            "YAML Files (*.yaml *.yml);;JSON Files (*.json);;All Files (*)"
            if format_type == "openapi"
            else "JSON Files (*.json);;All Files (*)"
        )

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            f"Export as {format_type.title()}",
            "",
            file_filter,
        )
        if not isinstance(file_path, str) or not file_path:
            return

        openapi_title = col_name
        if format_type == "openapi":
            title, ok = QInputDialog.getText(self, "OpenAPI Title", "API Title:", text=col_name)
            if not ok:
                return
            openapi_title = title

        self._start_export(
            format_type=format_type,
            collection_id=collection_id,
            file_path=Path(file_path),
            openapi_title=openapi_title,
        )

    def _start_export(
        self,
        format_type: str,
        collection_id: int,
        file_path: Path,
        openapi_title: str,
    ) -> None:
        """Run collection export in the background with retry support."""
        from equinox.exporters import InsomniaExporter, OpenAPIExporter, PostmanExporter

        def _operation(cancel_event=None) -> str:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Export cancelled")
            if format_type == "postman":
                data = PostmanExporter.export_collection(self.db, collection_id)
                PostmanExporter.export_to_file(data, file_path)
            elif format_type == "openapi":
                data = OpenAPIExporter.export_collection(self.db, collection_id, openapi_title)
                OpenAPIExporter.export_to_file(data, file_path)
            elif format_type == "insomnia":
                data = InsomniaExporter.export_collection(self.db, collection_id)
                InsomniaExporter.export_to_file(data, file_path)
            else:
                raise ValueError(f"Unsupported export format: {format_type}")
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Export cancelled")
            return str(file_path)

        self._run_background_task(
            operation=_operation,
            operation_name=f"Exporting {file_path.name}...",
            success_msg=f"Exported to {file_path}",
            error_title="Export Error",
            retry_operation=lambda: self._start_export(
                format_type=format_type,
                collection_id=collection_id,
                file_path=file_path,
                openapi_title=openapi_title,
            ),
        )
