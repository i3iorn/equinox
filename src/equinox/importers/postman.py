"""Postman collection importer with security validation.

Supports Postman Collection Format v2.0 and v2.1.
Collection-level variables (including ``{{baseUrl}}``) are resolved before
saving requests and persisted on the collection for reference.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

from equinox.core.request import Request
from equinox.core.exceptions import ValidationError, SecurityError
from equinox.core.validation import Validator
from equinox.storage.collections import CollectionManager

logger = logging.getLogger(__name__)


def _extract_collection_variables(collection_data: Dict[str, Any]) -> Dict[str, str]:
    """Extract collection-level variables from a Postman collection.

    Postman stores variables in ``collection.variable`` as a list of
    ``{key, value, type}`` objects.  We convert them to a plain dict.

    Args:
        collection_data: Full Postman collection dict.

    Returns:
        Mapping of variable name → current value (string).
    """
    variables: Dict[str, str] = {}
    for var in collection_data.get("variable", []):
        if not isinstance(var, dict):
            continue
        key = var.get("key", "")
        value = var.get("value", "")
        if key:
            variables[key] = str(value) if value is not None else ""
    return variables


def _resolve_postman_variable(value: str, variables: Dict[str, str]) -> str:
    """Resolve Postman ``{{variable}}`` expressions with up to 5 passes.

    Multiple passes handle chained references, e.g. ``{{baseUrl}}`` where
    ``baseUrl = "{{scheme}}://{{host}}"``.  Unresolvable references are left
    as-is so users can supply them via an active Equinox environment at runtime.

    Args:
        value:     String potentially containing ``{{varName}}`` tokens.
        variables: Collection-level variables.

    Returns:
        String with known variables substituted.
    """
    import re

    _PATTERN = re.compile(r"\{\{([^}]+)\}\}")

    def replace(match: "re.Match[str]") -> str:  # type: ignore[type-arg]
        return variables.get(match.group(1), match.group(0))

    for _ in range(5):
        new_value = _PATTERN.sub(replace, value)
        if new_value == value:
            break  # no more substitutions possible
        value = new_value

    return value


class PostmanImporter:
    """Import Postman collections with validation and sanitization."""

    SUPPORTED_VERSIONS = {"2.0.0", "2.1.0"}
    MAX_COLLECTION_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_REQUESTS = 1000

    def __init__(self, collection_manager: CollectionManager):
        """Initialize importer.

        Args:
            collection_manager: Collection manager for saving
        """
        self.collection_manager = collection_manager

    def import_file(self, file_path: Path) -> int:
        """Import Postman collection from file.

        Args:
            file_path: Path to Postman collection JSON

        Returns:
            Collection ID

        Raises:
            ValidationError: If collection is invalid
            SecurityError: If collection contains malicious content
        """
        self._validate_file(file_path)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                collection_data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Invalid JSON: {exc}")
        except Exception as exc:
            raise ValidationError(f"Failed to read file: {exc}")

        return self.import_dict(collection_data)

    def import_dict(self, collection_data: Dict[str, Any]) -> int:
        """Import Postman collection from dictionary.

        Collection-level variables (e.g. ``{{baseUrl}}``) are resolved when
        building request URLs.  Any that remain unresolvable are kept as
        Equinox ``{{variable}}`` placeholders so the user can supply them via
        an active environment at runtime.

        Args:
            collection_data: Postman collection data.

        Returns:
            Collection ID.
        """
        self._validate_collection(collection_data)

        info = collection_data.get("info", {})
        collection_name = info.get("name", "Imported Collection")
        collection_description = info.get("description", "")

        logger.info("Importing Postman collection: %s (format: %s)", 
                   collection_name, info.get("schema", "unknown"))

        col_variables = _extract_collection_variables(collection_data)
        if col_variables:
            logger.info("Found %d collection variable(s): %s",
                        len(col_variables), list(col_variables.keys()))

        collection_id = self.collection_manager.create_collection(
            name=collection_name,
            description=collection_description,
        )
        logger.info("Created collection: %s (ID: %d)", collection_name, collection_id)

        for var_name, var_value in col_variables.items():
            try:
                self.collection_manager.add_variable(
                    collection_id, var_name, var_value,
                    description="Imported from Postman collection variable",
                )
                logger.debug("Added collection variable: %s=%s", var_name, var_value)
            except Exception:
                pass  # add_variable may not exist on all manager builds

        items = collection_data.get("item", [])
        request_count = self._import_items(items, collection_id, col_variables=col_variables)
        logger.info("Imported %d requests", request_count)

        return collection_id

    def _validate_file(self, file_path: Path) -> None:
        """Validate collection file.

        Args:
            file_path: Path to file

        Raises:
            ValidationError: If file is invalid
        """
        if not file_path.exists():
            raise ValidationError(f"File not found: {file_path}")

        if file_path.suffix.lower() != ".json":
            raise ValidationError("File must be a JSON file")

        size = file_path.stat().st_size
        if size > self.MAX_COLLECTION_SIZE:
            raise ValidationError(
                f"Collection file too large: {size} bytes "
                f"(max: {self.MAX_COLLECTION_SIZE} bytes)"
            )

    def _validate_collection(self, collection_data: Dict[str, Any]) -> None:
        """Validate collection structure.

        Args:
            collection_data: Collection data

        Raises:
            ValidationError: If collection is invalid
        """
        if "info" not in collection_data:
            raise ValidationError("Collection missing 'info' field")

        info = collection_data["info"]

        if "name" not in info:
            raise ValidationError("Collection info missing 'name' field")

        if "schema" not in info:
            raise ValidationError("Collection missing 'schema' field")

        schema = info["schema"]

        # Schema URL format: https://schema.getpostman.com/json/collection/v2.1.0/collection.json
        if isinstance(schema, str):
            if "v2.0.0" in schema:
                version = "2.0.0"
            elif "v2.1.0" in schema:
                version = "2.1.0"
            else:
                raise ValidationError(f"Unsupported schema version: {schema}")
        else:
            raise ValidationError(f"Invalid schema format: {schema}")

        if version not in self.SUPPORTED_VERSIONS:
            raise ValidationError(
                f"Unsupported collection version: {version}. "
                f"Supported: {', '.join(self.SUPPORTED_VERSIONS)}"
            )

        items = collection_data.get("item", [])
        request_count = self._count_requests(items)
        if request_count > self.MAX_REQUESTS:
            raise ValidationError(
                f"Too many requests: {request_count} (max: {self.MAX_REQUESTS})"
            )

    def _count_requests(self, items: List[Dict[str, Any]]) -> int:
        """Count total requests in items.

        Args:
            items: List of items

        Returns:
            Total request count
        """
        count = 0

        for item in items:
            if "request" in item:
                count += 1
            elif "item" in item:
                count += self._count_requests(item["item"])

        return count

    def _import_items(
        self,
        items: List[Dict[str, Any]],
        collection_id: int,
        folder_name: str = "",
        col_variables: Optional[Dict[str, str]] = None,
    ) -> int:
        """Import items (requests and folders) recursively.

        Args:
            items:         List of Postman items.
            collection_id: Target collection ID.
            folder_name:   Accumulated folder path prefix.
            col_variables: Collection-level variable values for URL resolution.

        Returns:
            Number of requests imported.
        """
        if col_variables is None:
            col_variables = {}

        count = 0

        for item in items:
            if "request" in item:
                try:
                    request = self._parse_request(item, folder_name, col_variables)
                    self.collection_manager.save_request(request, collection_id)
                    count += 1
                except (ValidationError, SecurityError) as exc:
                    logger.warning("Skipping invalid request: %s", exc)

            elif "item" in item:
                folder = item.get("name", "Untitled Folder")
                prefix = f"{folder_name}/{folder}" if folder_name else folder
                count += self._import_items(
                    item["item"], collection_id, prefix, col_variables
                )

        return count

    def _parse_request(
        self,
        item: Dict[str, Any],
        folder_name: str,
        col_variables: Optional[Dict[str, str]] = None,
    ) -> Request:
        """Parse Postman request item.

        Args:
            item:          Postman request item.
            folder_name:   Folder path prefix for naming.
            col_variables: Collection variables used to resolve ``{{baseUrl}}``,
                           etc.  Unresolvable references are kept as-is.

        Returns:
            :class:`Request` object.
        """
        if col_variables is None:
            col_variables = {}

        request_data = item["request"]

        name = item.get("name", "Untitled Request")
        if folder_name:
            name = f"{folder_name}/{name}"

        if isinstance(request_data, str):
            # Simple string format (v2.0)
            method = "GET"
            url = _resolve_postman_variable(request_data, col_variables)
            headers: Dict[str, str] = {}
            body = None
            params_list = []
        else:
            method = request_data.get("method", "GET")

            url_data = request_data.get("url", {})
            params_list: list = []
            if isinstance(url_data, str):
                url = _resolve_postman_variable(url_data, col_variables)
            elif isinstance(url_data, dict):
                url, params_list = self._build_url(url_data, col_variables)
            else:
                raise ValidationError(f"Invalid URL format: {type(url_data)}")

            headers = {}
            for header in request_data.get("header", []):
                if header.get("disabled", False):
                    continue
                key = header.get("key", "")
                value = header.get("value", "")
                if key:
                    headers[key] = _resolve_postman_variable(value, col_variables)

            body = self._parse_body(request_data.get("body", {}))

        method = Validator.validate_method(method)
        url = Validator.validate_url(url)
        if headers:
            headers = Validator.validate_headers(headers)
        if body:
            content_type = headers.get("Content-Type", "")
            body = Validator.validate_request_body(body, content_type)

        description = item.get("description", "")
        if isinstance(description, dict):
            description = description.get("content", "")

        params = {r["key"]: r["value"] for r in params_list if r.get("enabled", True)}

        # Extract Postman event scripts → pre_script / post_script
        pre_script = ""
        post_script = ""
        for event in item.get("event", []):
            listen = event.get("listen", "")
            exec_lines = event.get("script", {}).get("exec", [])
            if isinstance(exec_lines, list):
                script_text = "\n".join(exec_lines)
            else:
                script_text = str(exec_lines)
            if not script_text.strip():
                continue
            # Wrap in a comment block noting the JavaScript origin
            header = f"# Imported from Postman {listen} script (JavaScript → review before use)\n"
            if listen == "prerequest":
                pre_script = header + script_text
            elif listen == "test":
                post_script = header + script_text

        return Request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            params_list=params_list if params_list else None,
            body=body,
            name=name,
            description=description,
            pre_script=pre_script or None,
            post_script=post_script or None,
        )

    def _build_url(
        self,
        url_data: Dict[str, Any],
        col_variables: Optional[Dict[str, str]] = None,
    ) -> tuple:
        """Build (base_url, params_list) from a Postman URL object.

        Query parameters are returned as a ``params_list`` — a list of
        ``{"key", "value", "enabled"}`` dicts — rather than being appended to
        the URL string.  This lets the GUI display them in the Params tab with
        per-row enable/disable toggles and variable interpolation at send time.

        When ``raw`` is present but there is no structured ``query`` array the
        raw URL is returned unchanged (params embedded) with an empty list.

        Args:
            url_data:      Postman URL object.
            col_variables: Collection-level variables for resolving tokens.

        Returns:
            ``(url: str, params_list: list)``
        """
        if col_variables is None:
            col_variables = {}

        query = url_data.get("query", [])

        # If there is no structured query array, fall back to the raw URL
        # (which may already have params embedded as a query string).
        if "raw" in url_data and not query:
            return _resolve_postman_variable(str(url_data["raw"]), col_variables), []

        # Build base URL from components (excluding query string)
        protocol = url_data.get("protocol", "https")
        host = url_data.get("host", [])
        path = url_data.get("path", [])

        host_str = ".".join(host) if isinstance(host, list) else str(host)
        host_str = _resolve_postman_variable(host_str, col_variables)

        path_parts = path if isinstance(path, list) else [str(path)]
        # Postman sometimes stores path segments as objects e.g. {type, value}
        path_str = "/" + "/".join(
            _resolve_postman_variable(
                p["value"] if isinstance(p, dict) else str(p), col_variables
            )
            for p in path_parts
        )

        base_url = f"{protocol}://{host_str}{path_str}"

        # Build structured params list preserving disabled state
        params_list = []
        for param in query:
            key = param.get("key", "")
            if not key:
                continue
            value = _resolve_postman_variable(str(param.get("value", "")), col_variables)
            enabled = not param.get("disabled", False)
            params_list.append({"key": key, "value": value, "enabled": enabled})

        return base_url, params_list

    def _parse_body(self, body_data: Dict[str, Any]) -> Optional[str]:
        """Parse request body from Postman format.

        Args:
            body_data: Body data

        Returns:
            Body string or None
        """
        if not body_data:
            return None

        mode = body_data.get("mode")

        if mode == "raw":
            return body_data.get("raw", "")

        if mode == "urlencoded":
            params = []
            for param in body_data.get("urlencoded", []):
                if param.get("disabled", False):
                    continue
                key = param.get("key", "")
                value = param.get("value", "")
                if key:
                    params.append(f"{key}={value}")
            return "&".join(params)

        if mode == "formdata":
            logger.warning("Form data not fully supported in import")
            return None

        if mode == "file":
            logger.warning("File upload not supported in import")
            return None

        if mode == "graphql":
            return body_data.get("graphql", {}).get("query", "")

        return None


def preview_collection(file_path: Path) -> Dict[str, Any]:
    """Preview collection before importing.

    Args:
        file_path: Path to collection file

    Returns:
        Dict with collection info

    Raises:
        ValidationError: If collection is invalid
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            collection_data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON: {exc}")

    info = collection_data.get("info", {})
    items = collection_data.get("item", [])
    request_count = _count_requests_recursive(items)

    return {
        "name": info.get("name", "Unknown"),
        "description": info.get("description", ""),
        "version": info.get("schema", "Unknown"),
        "request_count": request_count,
        "size_bytes": file_path.stat().st_size
    }


def _count_requests_recursive(items: List[Dict[str, Any]]) -> int:
    """Count requests recursively."""
    count = 0

    for item in items:
        if "request" in item:
            count += 1
        elif "item" in item:
            count += _count_requests_recursive(item["item"])

    return count
