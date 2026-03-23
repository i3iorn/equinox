"""OpenAPI/Swagger specification importer with security validation.

Supports OpenAPI 3.0.x and 2.0 (Swagger).
Multi-server handling: all defined servers are imported as separate
collections (or as requests with a {{baseUrl}} variable when a single
server selector is preferred).
"""

import json
import re
import yaml
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from equinox.core.request import Request
from equinox.core.exceptions import ValidationError, SecurityError
from equinox.core.validation import Validator
from equinox.storage.collections import CollectionManager

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Server resolution helpers
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ServerInfo:
    """Resolved server information."""

    url: str
    description: str = ""
    variables: Dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        desc = f" ({self.description})" if self.description else ""
        return f"{self.url}{desc}"


def _expand_server_variables(url: str, variables: Dict[str, Any]) -> str:
    """Expand OpenAPI server variable templates in a URL.

    Per the spec, server variables look like ``{variableName}``.
    Each variable object has a ``default`` and optional ``enum`` list.

    Args:
        url:       URL template, e.g. ``https://{host}/v{version}``
        variables: Server ``variables`` object from the spec.

    Returns:
        URL with all variables replaced by their defaults.
    """
    if not variables:
        return url

    def replace(match: re.Match) -> str:  # type: ignore[type-arg]
        var_name = match.group(1)
        var_def = variables.get(var_name, {})
        if isinstance(var_def, dict):
            return str(var_def.get("default", match.group(0)))
        return str(var_def)

    return re.sub(r"\{([^}]+)\}", replace, url)


def _resolve_servers_openapi3(spec_data: Dict[str, Any]) -> List[ServerInfo]:
    """Extract and resolve all servers from an OpenAPI 3.x spec.

    Falls back to ``/`` (relative root) when no servers block is present,
    which is the OpenAPI 3.x default.  For URL-validation purposes, relative
    roots are kept as ``/`` so callers that need absolute URLs can prepend
    their own scheme/host.

    Args:
        spec_data: Full parsed spec.

    Returns:
        Non-empty list of :class:`ServerInfo` objects.
    """
    raw_servers = spec_data.get("servers")

    # OpenAPI 3 spec §4.7.5: if omitted the default is [{"url": "/"}]
    if not raw_servers:
        return [ServerInfo(url="/")]

    result: List[ServerInfo] = []
    for entry in raw_servers:
        if not isinstance(entry, dict):
            continue
        raw_url = str(entry.get("url", "/"))
        # Strip trailing slash only when there's an actual path component
        # (i.e. not just "/").
        if raw_url != "/" and raw_url.endswith("/"):
            raw_url = raw_url[:-1]
        srv_vars = entry.get("variables") or {}
        expanded = _expand_server_variables(raw_url, srv_vars)

        resolved_vars = {
            name: (v.get("default", "") if isinstance(v, dict) else str(v))
            for name, v in srv_vars.items()
        }

        result.append(
            ServerInfo(
                url=expanded,
                description=entry.get("description", ""),
                variables=resolved_vars,
            )
        )

    return result or [ServerInfo(url="/")]


def _resolve_servers_swagger2(spec_data: Dict[str, Any]) -> List[ServerInfo]:
    """Extract all base URLs from a Swagger 2.0 spec.

    Swagger 2.0 supports multiple schemes (http/https) so we generate one
    :class:`ServerInfo` per scheme.

    Args:
        spec_data: Full parsed Swagger 2.0 spec.

    Returns:
        Non-empty list of :class:`ServerInfo` objects.
    """
    host = spec_data.get("host", "localhost")
    base_path = spec_data.get("basePath", "").rstrip("/")
    schemes = spec_data.get("schemes") or ["https"]

    return [
        ServerInfo(url=f"{scheme}://{host}{base_path}", description=f"{scheme} scheme")
        for scheme in schemes
    ]


class OpenAPIImporter:
    """Import OpenAPI/Swagger specifications with validation and sanitization.

    Multi-server behaviour
    ----------------------
    * **Single server** — requests are imported with the literal resolved URL.
    * **Multiple servers** — one collection is created per server.  Each
      collection name is suffixed with the server description or URL so users
      can easily tell them apart.  Additionally, a shared ``{{BASE_URL}}``
      variable is stored on every collection so the URLs can be switched
      without re-importing.
    """

    SUPPORTED_VERSIONS = {"2.0", "3.0", "3.0.0", "3.0.1", "3.0.2", "3.0.3", "3.1"}
    MAX_SPEC_SIZE = 10 * 1024 * 1024  # 10 MB
    MAX_PATHS = 500
    MAX_OPERATIONS = 1000

    def __init__(self, collection_manager: CollectionManager):
        self.collection_manager = collection_manager

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def import_file(self, file_path: Path) -> int:
        """Import OpenAPI spec from file.  Returns the *first* collection ID."""
        self._validate_file(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                content = fh.read()
            try:
                spec_data = yaml.safe_load(content)
            except yaml.YAMLError:
                spec_data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Invalid JSON/YAML: {exc}")
        except Exception as exc:
            raise ValidationError(f"Failed to read file: {exc}")
        return self.import_dict(spec_data)

    def import_dict(self, spec_data: Dict[str, Any]) -> int:
        """Import OpenAPI spec from a dictionary.

        When the spec defines multiple servers every server is imported as its
        own collection.  The first collection ID is returned for backwards
        compatibility.

        Args:
            spec_data: Parsed OpenAPI / Swagger data.

        Returns:
            ID of the first (or only) collection created.
        """
        self._validate_spec(spec_data)
        version = self._get_version(spec_data)
        logger.info("Importing OpenAPI %s specification", version)

        info = spec_data.get("info", {})
        base_name = info.get("title", "Imported API")
        base_desc = info.get("description", "")
        logger.debug("OpenAPI spec: title=%s version=%s", base_name, info.get("version"))

        servers = self._get_servers(spec_data, version)
        logger.info("OpenAPI spec defines %d server(s)", len(servers))
        for i, server in enumerate(servers, 1):
            logger.debug("Server %d: url=%s desc=%s", i, server.url, server.description)
        logger.info("Found %d server(s)", len(servers))

        first_id: Optional[int] = None

        for server in servers:
            # Build a human-readable collection name that includes the server
            # when there are multiple of them.
            if len(servers) > 1:
                suffix = server.description or server.url
                col_name = f"{base_name} — {suffix}"
            else:
                col_name = base_name

            # Store BASE_URL as a collection variable so users can switch
            col_desc = base_desc
            if server.description:
                col_desc = f"{base_desc}\nServer: {server.description}".strip()

            collection_id = self.collection_manager.create_collection(
                name=col_name,
                description=col_desc,
            )

            # Persist BASE_URL as a collection variable for easy re-targeting
            try:
                self.collection_manager.add_variable(
                    collection_id, "BASE_URL", server.url,
                    description=f"Server base URL — {server.description or server.url}",
                )
            except Exception:
                # add_variable may not exist on all manager versions; skip
                pass

            logger.info("Created collection '%s' (ID %d) for server %s",
                        col_name, collection_id, server.url)

            paths = spec_data.get("paths") or {}
            count = self._import_paths(spec_data, paths, collection_id, version, server)
            logger.info("Imported %d endpoints for server %s", count, server.url)

            # OAS 3.1: also import webhooks alongside regular paths
            webhooks = spec_data.get("webhooks") or {}
            if webhooks and isinstance(webhooks, dict) and version.startswith("3."):
                wh_count = self._import_paths(spec_data, webhooks, collection_id, version, server)
                if wh_count:
                    logger.info("Imported %d webhook(s) for server %s", wh_count, server.url)
                count += wh_count

            if first_id is None:
                first_id = collection_id

        return first_id  # type: ignore[return-value]

    # ──────────────────────────────────────────────────────────────────────
    # Server resolution
    # ──────────────────────────────────────────────────────────────────────

    def _get_servers(self, spec_data: Dict[str, Any], version: str) -> List[ServerInfo]:
        """Return resolved server list for any supported OpenAPI/Swagger version."""
        if version.startswith("3."):
            return _resolve_servers_openapi3(spec_data)
        else:
            return _resolve_servers_swagger2(spec_data)

    # kept for backwards-compat; callers should prefer _get_servers
    def _get_base_url(self, spec_data: Dict[str, Any], version: str) -> str:
        """Return the *first* resolved base URL (legacy helper)."""
        servers = self._get_servers(spec_data, version)
        return servers[0].url if servers else "https://api.example.com"



    def _validate_file(self, file_path: Path) -> None:
        """Validate spec file.

        Args:
            file_path: Path to file

        Raises:
            ValidationError: If file is invalid
        """
        if not file_path.exists():
            raise ValidationError(f"File not found: {file_path}")

        if file_path.suffix.lower() not in [".json", ".yaml", ".yml"]:
            raise ValidationError("File must be JSON or YAML")

        size = file_path.stat().st_size
        if size > self.MAX_SPEC_SIZE:
            raise ValidationError(
                f"Spec file too large: {size} bytes "
                f"(max: {self.MAX_SPEC_SIZE} bytes)"
            )

    def _validate_spec(self, spec_data: Dict[str, Any]) -> None:
        """Validate OpenAPI spec structure.

        Args:
            spec_data: Spec data

        Raises:
            ValidationError: If spec is invalid
        """
        if not isinstance(spec_data, dict):
            raise ValidationError("Spec must be a dictionary")

        version = self._get_version(spec_data)
        if version not in self.SUPPORTED_VERSIONS:
            if "." in version:
                major_minor = ".".join(version.split(".")[:2])
                if major_minor not in self.SUPPORTED_VERSIONS:
                    raise ValidationError(
                        f"Unsupported OpenAPI version: {version}. "
                        f"Supported: {', '.join(sorted(self.SUPPORTED_VERSIONS))}"
                    )
            else:
                raise ValidationError(f"Invalid version format: {version}")

        paths = spec_data.get("paths", {})
        if not isinstance(paths, dict):
            raise ValidationError("Paths must be a dictionary")
        if len(paths) > self.MAX_PATHS:
            raise ValidationError(
                f"Too many paths: {len(paths)} (max: {self.MAX_PATHS})"
            )

        # Count total operations (paths + webhooks for OAS 3.1)
        http_methods = ["get", "post", "put", "patch", "delete", "head", "options"]
        operation_count = 0
        for path_data in paths.values():
            if isinstance(path_data, dict):
                operation_count += sum(1 for m in http_methods if m in path_data)

        webhooks = spec_data.get("webhooks") or {}
        if isinstance(webhooks, dict):
            for wh_data in webhooks.values():
                if isinstance(wh_data, dict):
                    operation_count += sum(1 for m in http_methods if m in wh_data)

        if operation_count > self.MAX_OPERATIONS:
            raise ValidationError(
                f"Too many operations: {operation_count} (max: {self.MAX_OPERATIONS})"
            )

    def _get_version(self, spec_data: Dict[str, Any]) -> str:
        """Get OpenAPI version.

        Args:
            spec_data: Spec data

        Returns:
            Version string

        Raises:
            ValidationError: If version not found
        """
        if "openapi" in spec_data:
            return str(spec_data["openapi"])
        if "swagger" in spec_data:
            return str(spec_data["swagger"])

        raise ValidationError("Missing 'openapi' or 'swagger' version field")

    def _import_paths(
        self,
        spec_data: Dict[str, Any],
        paths: Dict[str, Any],
        collection_id: int,
        version: str,
        server: Optional[ServerInfo] = None,
    ) -> int:
        """Import API paths/endpoints.

        Args:
            spec_data:     Full spec data.
            paths:         Paths dictionary.
            collection_id: Target collection ID.
            version:       OpenAPI version string.
            server:        Resolved server to use for building URLs.
                           Falls back to legacy ``_get_base_url`` when None.

        Returns:
            Number of requests imported.
        """
        count = 0
        base_url = server.url if server else self._get_base_url(spec_data, version)

        # A purely relative base_url (e.g. "/") can't pass URL validation.
        # Promote it to a placeholder host so the path is still importable
        # and the user can update {{BASE_URL}} later.
        if base_url == "/" or not base_url.startswith(("http://", "https://")):
            base_url = "https://{{BASE_URL}}"

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            http_methods = ["get", "post", "put", "patch", "delete", "head", "options"]

            for method in http_methods:
                if method in path_item:
                    operation = path_item[method]
                    try:
                        request = self._parse_operation(
                            path, method, operation, base_url, spec_data, version
                        )
                        self.collection_manager.save_request(request, collection_id)
                        count += 1
                    except (ValidationError, SecurityError) as exc:
                        logger.warning("Skipping %s %s: %s", method.upper(), path, exc)

        return count


    def _get_base_url(self, spec_data: Dict[str, Any], version: str) -> str:
        """Get base URL from spec.

        Args:
            spec_data: Spec data
            version: OpenAPI version

        Returns:
            Base URL
        """
        if version.startswith("3."):
            servers = spec_data.get("servers", [])
            if servers and isinstance(servers, list) and len(servers) > 0:
                server = servers[0]
                if isinstance(server, dict) and "url" in server:
                    return server["url"]
        elif version == "2.0":
            schemes = spec_data.get("schemes", ["https"])
            host = spec_data.get("host", "api.example.com")
            base_path = spec_data.get("basePath", "")

            scheme = schemes[0] if schemes else "https"
            return f"{scheme}://{host}{base_path}"

        return "https://api.example.com"

    def _parse_operation(
        self,
        path: str,
        method: str,
        operation: Dict[str, Any],
        base_url: str,
        spec_data: Dict[str, Any],
        version: str
    ) -> Request:
        """Parse OpenAPI operation to Request.

        Args:
            path: API path (e.g., /users/{id})
            method: HTTP method
            operation: Operation object
            base_url: Base URL
            spec_data: Full spec (for definitions)
            version: OpenAPI version

        Returns:
            Request object

        Raises:
            ValidationError: If operation is invalid
        """
        operation_id = operation.get("operationId", "")
        summary = operation.get("summary", "")
        name = operation_id or summary or f"{method.upper()} {path}"
        description = operation.get("description", "")

        url = Validator.validate_url(base_url + path)

        headers: Dict[str, str] = {}
        params: Dict[str, str] = {}
        path_params: Dict[str, str] = {}
        body = None

        for param in operation.get("parameters", []):
            if not isinstance(param, dict):
                continue
            param_name = param.get("name", "")
            param_in = param.get("in", "")
            example = self._get_parameter_example(param, version)

            if param_in == "header":
                headers[param_name] = example
            elif param_in == "query":
                params[param_name] = example
            elif param_in == "path":
                # Replace {param} with {{param}} for Equinox variable interpolation
                url = url.replace(f"{{{param_name}}}", f"{{{{{param_name}}}}}")
                path_params[param_name] = example

        if version.startswith("3."):
            request_body = operation.get("requestBody", {})
            if request_body:
                body = self._parse_request_body(request_body)

        if headers:
            headers = Validator.validate_headers(headers)
        if body:
            content_type = headers.get("Content-Type", "application/json")
            body = Validator.validate_request_body(body, content_type)

        return Request(
            method=method.upper(),
            url=url,
            headers=headers,
            params=params,
            body=body,
            name=name,
            description=description,
            path_params=path_params,
        )

    @staticmethod
    def _resolve_schema_type(schema: Dict[str, Any]) -> str:
        """Resolve the effective scalar type of a JSON Schema object.

        Handles OAS 3.1 extensions:
        - ``type`` as a list (e.g. ``["string", "null"]``) — picks first non-null.
        - ``const`` — infers type from the constant value.
        - ``oneOf`` / ``anyOf`` / ``allOf`` — delegates to first sub-schema.
        """
        # const: single-value constraint (OAS 3.1 replaces single-element enum)
        if "const" in schema:
            v = schema["const"]
            if isinstance(v, bool):
                return "boolean"
            if isinstance(v, int):
                return "integer"
            if isinstance(v, float):
                return "number"
            if isinstance(v, list):
                return "array"
            if isinstance(v, dict):
                return "object"
            return "string"

        # Composites: delegate to first sub-schema
        for kw in ("oneOf", "anyOf", "allOf"):
            sub = schema.get(kw)
            if sub and isinstance(sub, list) and isinstance(sub[0], dict):
                return OpenAPIImporter._resolve_schema_type(sub[0])

        schema_type = schema.get("type", "object")

        # OAS 3.1: type can be a list, e.g. ["string", "null"]
        if isinstance(schema_type, list):
            non_null = [t for t in schema_type if t != "null"]
            return non_null[0] if non_null else "string"

        return str(schema_type)

    def _get_parameter_example(self, param: Dict[str, Any], version: str) -> str:
        """Get example value for parameter.

        Args:
            param: Parameter object
            version: OpenAPI version

        Returns:
            Example value as string
        """
        # Try example field
        if "example" in param:
            return str(param["example"])

        # Try default
        if "default" in param:
            return str(param["default"])

        # OAS 3.x stores type under schema sub-object; also handle type lists (3.1)
        schema = param.get("schema") or {}
        if isinstance(schema, dict) and "example" in schema:
            return str(schema["example"])
        if isinstance(schema, dict) and "default" in schema:
            return str(schema["default"])

        # Resolve type — prefer schema.type (3.x style) over param.type (2.0 style)
        raw_type = (
            schema.get("type") if isinstance(schema, dict) else None
        ) or param.get("type", "string")

        if isinstance(raw_type, list):
            non_null = [t for t in raw_type if t != "null"]
            param_type = non_null[0] if non_null else "string"
        else:
            param_type = str(raw_type)

        if param_type == "string":
            return "string"
        elif param_type == "integer":
            return "0"
        elif param_type == "number":
            return "0.0"
        elif param_type == "boolean":
            return "true"
        elif param_type == "array":
            return "[]"
        elif param_type == "object":
            return "{}"

        return "value"

    def _parse_request_body(self, request_body: Dict[str, Any]) -> Optional[str]:
        """Parse OpenAPI 3.x request body.

        Args:
            request_body: RequestBody object

        Returns:
            Body string or None
        """
        content = request_body.get("content", {})

        # Try JSON first
        for content_type in ["application/json", "application/xml", "text/plain"]:
            if content_type in content:
                media_type = content[content_type]

                # Try example
                if "example" in media_type:
                    return json.dumps(media_type["example"])

                # Try schema with example
                schema = media_type.get("schema", {})
                if "example" in schema:
                    return json.dumps(schema["example"])

                # Generate from schema
                return self._generate_example_from_schema(schema)

        return None

    def _generate_example_from_schema(self, schema: Dict[str, Any]) -> str:
        """Generate example JSON from schema.

        Handles OAS 3.1 extensions: type arrays, const, oneOf/anyOf/allOf.

        Args:
            schema: JSON Schema

        Returns:
            Example JSON string
        """
        # const: single fixed value — use it directly
        if "const" in schema:
            return json.dumps(schema["const"])

        schema_type = self._resolve_schema_type(schema)

        if schema_type == "object":
            example = {}
            properties = schema.get("properties", {})
            for prop_name, prop_schema in properties.items():
                if not isinstance(prop_schema, dict):
                    continue
                if "example" in prop_schema:
                    example[prop_name] = prop_schema["example"]
                elif "const" in prop_schema:
                    example[prop_name] = prop_schema["const"]
                elif "default" in prop_schema:
                    example[prop_name] = prop_schema["default"]
                else:
                    example[prop_name] = self._get_type_example(
                        self._resolve_schema_type(prop_schema)
                    )
            return json.dumps(example)

        elif schema_type == "array":
            return "[]"

        return "{}"

    def _get_type_example(self, schema_type: str) -> Any:
        """Get example value for schema type.

        Args:
            schema_type: Schema type

        Returns:
            Example value
        """
        type_examples = {
            "string": "string",
            "integer": 0,
            "number": 0.0,
            "boolean": True,
            "array": [],
            "object": {}
        }

        return type_examples.get(schema_type, "value")


def preview_spec(file_path: Path) -> Dict[str, Any]:
    """Preview OpenAPI spec before importing.

    Args:
        file_path: Path to spec file

    Returns:
        Dict with spec info including resolved servers.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        try:
            spec_data = yaml.safe_load(content)
        except yaml.YAMLError:
            spec_data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON/YAML: {exc}")

    info = spec_data.get("info", {})
    version = spec_data.get("openapi") or spec_data.get("swagger", "Unknown")

    paths = spec_data.get("paths", {})
    operation_count = 0
    for path_data in paths.values():
        if isinstance(path_data, dict):
            methods = ["get", "post", "put", "patch", "delete", "head", "options"]
            operation_count += sum(1 for m in methods if m in path_data)

    # Resolve servers
    version_str = str(version)
    if version_str.startswith("3."):
        servers = _resolve_servers_openapi3(spec_data)
    else:
        servers = _resolve_servers_swagger2(spec_data)

    return {
        "title": info.get("title", "Unknown"),
        "description": info.get("description", ""),
        "version": info.get("version", ""),
        "openapi_version": version,
        "path_count": len(paths),
        "operation_count": operation_count,
        "size_bytes": file_path.stat().st_size,
        "servers": [{"url": s.url, "description": s.description} for s in servers],
        "server_count": len(servers),
    }
