"""Insomnia v4 collection importer."""

import logging
from pathlib import Path
from typing import Any, Callable

from equinox.core.request import Request
from equinox.importers._utils import normalize_path_variables, validate_import_file
from equinox.storage.collections import CollectionManager
from equinox.storage.utils import safe_json_loads

logger = logging.getLogger(__name__)


class InsomniaImporter:
    """Import an Insomnia v4 export file as an Equinox collection.

    Insomnia v4 exports are JSON files containing a ``resources`` array.
    Each resource has a ``_type`` field: ``"workspace"``, ``"request_group"``
    (folder), or ``"request"``.  Requests reference their parent via
    ``parentId``.

    Usage::

        from equinox.storage import CollectionManager
        mgr = CollectionManager(db)
        importer = InsomniaImporter(mgr)
        importer.import_file(Path("export.json"))
    """

    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
    MAX_REQUESTS = 5000

    def __init__(self, collection_manager: CollectionManager) -> None:
        self.manager = collection_manager

    # ── Public API ────────────────────────────────────────────────────

    def import_file(self, path: Path) -> None:
        """Parse an Insomnia v4 JSON export and save to the database.

        Args:
            path: Path to the .json Insomnia export file.

        Raises:
            ValueError: If the file is not a recognisable Insomnia export.
            OSError: If the file cannot be read.
        """
        path = Path(path)
        try:
            validate_import_file(path, self.MAX_FILE_SIZE, label="Insomnia file")
        except Exception as exc:
            raise ValueError(str(exc)) from exc

        text = path.read_text(encoding="utf-8")
        data = safe_json_loads(text)
        if not isinstance(data, dict):
            raise ValueError("Invalid Insomnia export JSON")
        self._import_data(data)

    # ── Internal helpers ──────────────────────────────────────────────

    def _import_data(self, data: dict[str, Any]) -> None:
        resources = data.get("resources", [])
        if not resources:
            raise ValueError("No resources found in the Insomnia export")

        # Find workspace → collection name
        workspaces = [r for r in resources if r.get("_type") == "workspace"]
        workspace_name = workspaces[0]["name"] if workspaces else "Insomnia Import"
        workspace_id = workspaces[0].get("_id", "") if workspaces else ""

        col_id = self.manager.create_collection(workspace_name)
        logger.info("Created collection '%s' (id=%d)", workspace_name, col_id)

        # Build folder lookup: _id → resource dict
        folders: dict[str, dict[str, Any]] = {
            r["_id"]: r for r in resources if r.get("_type") == "request_group"
        }

        def get_folder_path(resource_id: str) -> str:
            """Recursively build the slash-delimited folder path."""
            if resource_id not in folders:
                return ""
            folder = folders[resource_id]
            parent_id = folder.get("parentId", "")
            # Stop recursing when we hit the workspace root
            if parent_id == workspace_id or parent_id not in folders:
                return str(folder.get("name", ""))
            parent_path = get_folder_path(parent_id)
            name = str(folder.get("name", ""))
            return f"{parent_path}/{name}" if parent_path else name

        request_resources = [r for r in resources if r.get("_type") == "request"]
        if len(request_resources) > self.MAX_REQUESTS:
            raise ValueError(
                f"Too many requests: {len(request_resources)} (max {self.MAX_REQUESTS})"
            )

        imported = 0
        for res in request_resources:
            self._import_request(res, col_id, folders, workspace_id, get_folder_path)
            imported += 1

        logger.info("Imported %d request(s) into collection '%s'", imported, workspace_name)

    def _import_request(
        self,
        res: dict[str, Any],
        col_id: int,
        folders: dict[str, dict[str, Any]],
        workspace_id: str,
        get_folder_path: Callable[[str], str],
    ) -> None:
        """Convert one Insomnia request resource into an Equinox Request and save it."""
        parent_id = res.get("parentId", "")
        folder_path = get_folder_path(parent_id) if parent_id != workspace_id else ""

        # Headers: list of {"name": "...", "value": "...", "disabled": bool}
        raw_headers = res.get("headers") or []
        headers = {
            h["name"]: h["value"]
            for h in raw_headers
            if not h.get("disabled", False) and h.get("name")
        }

        # Query parameters
        raw_params = res.get("parameters") or []
        params = {
            p["name"]: p["value"]
            for p in raw_params
            if not p.get("disabled", False) and p.get("name")
        }

        # Body
        body_obj = res.get("body") or {}
        body: str | None = None
        if isinstance(body_obj, dict):
            if body_obj.get("text"):
                body = body_obj["text"]
            elif body_obj.get("params"):
                # form-urlencoded
                body = "&".join(
                    f"{p['name']}={p.get('value', '')}"
                    for p in body_obj["params"]
                    if not p.get("disabled", False)
                )
                if "Content-Type" not in headers:
                    headers["Content-Type"] = "application/x-www-form-urlencoded"

        # GraphQL: Insomnia stores query/variables under body.text as JSON
        if body_obj.get("mimeType") == "application/graphql":
            body = body_obj.get("text", "")

        request = Request(
            method=res.get("method", "GET").upper(),
            url=normalize_path_variables(res.get("url", "")),
            headers=headers,
            params=params,
            body=body,
            name=res.get("name") or "Unnamed",
            description=res.get("description") or "",
            collection_id=col_id,
            folder=folder_path or None,
        )
        self.manager.save_request(request, collection_id=col_id, name=request.name)
