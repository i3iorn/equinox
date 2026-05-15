"""API-spec export service for collection panel dialogs."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

from equinox.exporters import CurlExporter, OpenAPIExporter, PostmanExporter
from equinox.storage import CollectionManager, Database


@dataclass(frozen=True)
class ApiSpecPayload:
    """Dialog payload containing title and export variants."""

    title: str
    variants: Dict[str, str]


class ApiSpecExportService:
    """Build API spec variants for collection/request dialogs."""

    def __init__(self, db: Database, logger_: Optional[logging.Logger] = None):
        self._db = db
        self._logger = logger_ or logging.getLogger(__name__)
        self._mgr = CollectionManager(db)

    def build_collection_payload(self, collection_id: int) -> ApiSpecPayload:
        if isinstance(collection_id, bool) or not (isinstance(collection_id, int) and collection_id > 0):
            raise ValueError("Collection not specified or invalid.")

        coll = self._get_collection(collection_id)
        title = f"API Spec - {coll.get('name', 'Collection')}"
        variants = self._export_collection_variants(collection_id, coll)
        return ApiSpecPayload(title=title, variants=variants)

    def build_request_payload(self, request_id: int) -> ApiSpecPayload:
        if isinstance(request_id, bool) or not (isinstance(request_id, int) and request_id > 0):
            raise ValueError("Request not specified or invalid.")

        req = self._mgr.get_request(request_id)
        if not req:
            raise ValueError("Request not found.")

        title = f"API Spec - {req.name or 'Request'}"
        variants: Dict[str, str] = {}
        errors: List[str] = []

        try:
            variants["cURL"] = CurlExporter.export_request(req)
        except Exception as exc:
            self._logger.exception("spec_export.request.curl_failed request_id=%s", request_id)
            variants["cURL"] = ""
            errors.append(f"cURL export failed: {exc}")

        try:
            oa = OpenAPIExporter.export_collection(
                self._db,
                req.collection_id or 0,
                title=req.name or "API",
            )
            variants["OpenAPI 3 (JSON)"] = json.dumps(oa, indent=2, ensure_ascii=False)
        except Exception as exc:
            self._logger.exception("spec_export.request.openapi_failed request_id=%s", request_id)
            variants["OpenAPI 3 (JSON)"] = ""
            errors.append(f"OpenAPI export failed: {exc}")

        if not any(variants.values()):
            info = "Could not generate any spec for this request."
            if errors:
                info = f"{info}\n\n" + "\n".join(errors)
            variants = {"Info": info}

        return ApiSpecPayload(title=title, variants=variants)

    def _get_collection(self, collection_id: int) -> Dict[str, Any]:
        coll = self._mgr.get_collection(collection_id)
        if isinstance(coll, dict):
            return coll
        if isinstance(coll, list):
            normalized = self._coerce_collection_list(collection_id, coll)
            if normalized:
                return normalized
        raise ValueError("Collection not found.")

    def _coerce_collection_list(self, collection_id: int, coll: List[Any]) -> Dict[str, Any]:
        self._logger.warning(
            "spec_export.collection.coerce_list collection_id=%s",
            collection_id,
        )
        for entry in coll:
            if isinstance(entry, dict) and (entry.get("id") == collection_id or entry.get("name")):
                return entry
        for entry in coll:
            if isinstance(entry, dict):
                return entry
        return {"name": f"Collection {collection_id}", "items": coll}

    def _export_collection_variants(self, collection_id: int, coll: Dict[str, Any]) -> Dict[str, str]:
        variants: Dict[str, str] = {}
        errors: List[str] = []
        reqs = self._mgr.list_requests(collection_id)

        try:
            oa = OpenAPIExporter.export_collection(
                self._db,
                collection_id,
                title=coll.get("name", "API"),
            )
            variants["OpenAPI 3 (JSON)"] = json.dumps(oa, indent=2, ensure_ascii=False)
        except Exception as exc:
            self._logger.exception("spec_export.collection.openapi_failed collection_id=%s", collection_id)
            variants["OpenAPI 3 (JSON)"] = json.dumps(
                self._fallback_openapi(coll, reqs),
                indent=2,
                ensure_ascii=False,
            )
            errors.append(f"OpenAPI export failed: {exc}")

        try:
            pm = PostmanExporter.export_collection(self._db, collection_id)
            variants["Postman v2.1 (JSON)"] = json.dumps(pm, indent=2, ensure_ascii=False)
        except Exception as exc:
            self._logger.exception("spec_export.collection.postman_failed collection_id=%s", collection_id)
            variants["Postman v2.1 (JSON)"] = json.dumps(
                self._fallback_postman(coll, reqs),
                indent=2,
                ensure_ascii=False,
            )
            errors.append(f"Postman export failed: {exc}")

        if not any(variants.values()):
            variants = self._fallback_raw_variants(coll, reqs, errors)

        return variants

    @staticmethod
    def _request_url(row: Any) -> str:
        if not isinstance(row, dict):
            return ""
        for key in ("url", "raw_url", "full_url", "path"):
            value = row.get(key)
            if value:
                return value
        return row.get("name") or ""

    def _fallback_openapi(self, coll: Dict[str, Any], reqs: List[Any]) -> Dict[str, Any]:
        paths: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for row in reqs:
            url = self._request_url(row) or "/"
            method = (row.get("method") or "get").lower() if isinstance(row, dict) else "get"
            if url not in paths:
                paths[url] = {}
            paths[url][method] = {
                "summary": (row.get("name") or "") if isinstance(row, dict) else "",
                "responses": {"200": {"description": "OK"}},
            }

        return {
            "openapi": "3.0.0",
            "info": {"title": coll.get("name", "Collection"), "version": "1.0.0"},
            "paths": paths,
        }

    def _fallback_postman(self, coll: Dict[str, Any], reqs: List[Any]) -> Dict[str, Any]:
        items: List[Dict[str, Any]] = []
        for row in reqs:
            if not isinstance(row, dict):
                continue
            items.append(
                {
                    "name": row.get("name") or "",
                    "request": {
                        "method": row.get("method") or "GET",
                        "url": self._request_url(row) or "",
                    },
                }
            )

        return {
            "info": {
                "name": coll.get("name", "Collection"),
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": items,
        }

    def _fallback_raw_variants(
        self,
        coll: Dict[str, Any],
        reqs: List[Any],
        errors: List[str],
    ) -> Dict[str, str]:
        variants: Dict[str, str] = {}
        try:
            variants["Raw Collection"] = json.dumps(coll, indent=2, ensure_ascii=False)
        except Exception:
            self._logger.exception("spec_export.collection.raw_collection_failed")

        if reqs:
            try:
                variants["Raw Requests"] = json.dumps(reqs, indent=2, ensure_ascii=False)
            except Exception:
                variants["Raw Requests"] = repr(reqs)

        if any(variants.values()):
            return variants

        info = "Could not generate any spec for this collection."
        if errors:
            info = f"{info}\n\n" + "\n".join(errors)
        return {"Info": info}


