"""Plugin security policy guards.

This module provides security features for plugins including:
- Permission system
- Resource limits
- In-process execution guards
- Security validation
"""

import ast
import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, cast

from equinox.core.audit import get_audit_logger
from equinox.core.exceptions import SecurityError

logger = logging.getLogger(__name__)
audit_logger = get_audit_logger()

_DANGEROUS_MODULES = frozenset(
    {
        "subprocess",
        "shutil",
        "ctypes",
        "multiprocessing",
        "signal",
        "resource",
        "pty",
        "fcntl",
        "termios",
        "importlib",
        "code",
        "codeop",
        "dis",
        "inspect",
        "gc",
        "_thread",
        "socket",
        "http",
        "xmlrpc",
        "pickle",
        "shelve",
        "marshal",
        "builtins",
        "io",
        "webbrowser",
        "zipimport",
        "runpy",
    }
)

_OS_FORBIDDEN = frozenset(
    {
        "system",
        "popen",
        "exec",
        "execv",
        "execve",
        "spawn",
        "spawnl",
        "spawnle",
        "fork",
    }
)

_DANGEROUS_FUNCTIONS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "breakpoint",
    }
)


class Permission(Enum):
    """Plugin permissions."""

    # Network permissions
    NETWORK_HTTP = "network.http"  # Make HTTP requests
    NETWORK_HTTPS = "network.https"  # Make HTTPS requests
    NETWORK_WEBSOCKET = "network.websocket"  # WebSocket connections

    # File system permissions
    FILE_READ = "file.read"  # Read files
    FILE_WRITE = "file.write"  # Write files
    FILE_DELETE = "file.delete"  # Delete files

    # Storage permissions
    STORAGE_READ = "storage.read"  # Read from database
    STORAGE_WRITE = "storage.write"  # Write to database
    STORAGE_DELETE = "storage.delete"  # Delete from database

    # Credential permissions
    CREDENTIAL_READ = "credential.read"  # Read credentials
    CREDENTIAL_WRITE = "credential.write"  # Write credentials

    # System permissions
    SYSTEM_EXECUTE = "system.execute"  # Execute system commands
    SYSTEM_ENV = "system.env"  # Access environment variables

    # Request/Response permissions
    REQUEST_MODIFY = "request.modify"  # Modify outgoing requests
    RESPONSE_MODIFY = "response.modify"  # Modify incoming responses


@dataclass
class PluginManifest:
    """Plugin manifest with metadata and permissions."""

    name: str
    version: str
    author: str
    description: str = ""
    permissions: set[Permission] = field(default_factory=set)
    homepage: str = ""
    license: str = ""
    checksum: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginManifest":
        """Create manifest from dictionary.

        Args:
            data: Manifest data

        Returns:
            PluginManifest instance

        Raises:
            ValidationError: If manifest is invalid
        """
        if not isinstance(data, dict):
            raise SecurityError("Plugin manifest must be a JSON object")

        # Convert permission strings to Permission enum (deny unknown values)
        permissions = set()
        raw_permissions = data.get("permissions", [])
        if not isinstance(raw_permissions, list):
            raise SecurityError("Plugin manifest field 'permissions' must be a list")

        for perm_str in raw_permissions:
            if not isinstance(perm_str, str):
                raise SecurityError("Plugin manifest permissions must be strings")
            try:
                perm = Permission(perm_str)
                permissions.add(perm)
            except ValueError:
                raise SecurityError(f"Unknown plugin permission: {perm_str}")

        return cls(
            name=data["name"],
            version=data["version"],
            author=data["author"],
            description=data.get("description", ""),
            permissions=permissions,
            homepage=data.get("homepage", ""),
            license=data.get("license", ""),
            checksum=data.get("checksum"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "permissions": [p.value for p in self.permissions],
            "homepage": self.homepage,
            "license": self.license,
            "checksum": self.checksum,
        }


@dataclass
class ResourceLimits:
    """Resource limits for plugin execution."""

    max_memory_mb: int = 100  # Maximum memory usage in MB
    max_execution_time_ms: int = 5000  # Maximum execution time in ms
    max_file_size_mb: int = 10  # Maximum file size to read/write
    max_network_requests: int = 100  # Maximum network requests
    max_storage_operations: int = 100  # Maximum database operations

    def __post_init__(self) -> None:
        """Validate limits."""
        if self.max_memory_mb < 1 or self.max_memory_mb > 1000:
            raise ValueError("max_memory_mb must be between 1 and 1000")

        if self.max_execution_time_ms < 100 or self.max_execution_time_ms > 60000:
            raise ValueError("max_execution_time_ms must be between 100 and 60000")


class PluginSandbox:
    """In-process policy guard for plugin execution.

    This helper enforces declared permissions and resource limits, but does not
    provide process-level isolation.
    """

    def __init__(self, manifest: PluginManifest, limits: Optional[ResourceLimits] = None):
        """Initialize plugin execution guard.

        Args:
            manifest: Plugin manifest
            limits: Resource limits (default limits if None)
        """
        self.manifest = manifest
        self.limits = limits or ResourceLimits()

        # Tracking
        self._network_requests = 0
        self._storage_operations = 0
        self._start_time: Optional[float] = None

        # Audit
        audit_logger.log_plugin_event(manifest.name, "loaded", user="system")

    def check_permission(self, permission: Permission) -> None:
        """Check if plugin has permission.

        Args:
            permission: Permission to check

        Raises:
            SecurityError: If permission not granted
        """
        if permission not in self.manifest.permissions:
            error = f"Plugin '{self.manifest.name}' missing permission: {permission.value}"
            logger.error(error)

            audit_logger.log_security_violation(
                "plugin_permission",
                {"plugin": self.manifest.name, "permission": permission.value, "action": "denied"},
            )

            raise SecurityError(error)

    def check_network_request(self) -> None:
        """Check if plugin can make another network request.

        Raises:
            SecurityError: If limit exceeded
        """
        self._network_requests += 1

        if self._network_requests > self.limits.max_network_requests:
            raise SecurityError(
                f"Plugin '{self.manifest.name}' exceeded network request limit: "
                f"{self.limits.max_network_requests}"
            )

    def check_storage_operation(self) -> None:
        """Check if plugin can perform another storage operation.

        Raises:
            SecurityError: If limit exceeded
        """
        self._storage_operations += 1

        if self._storage_operations > self.limits.max_storage_operations:
            raise SecurityError(
                f"Plugin '{self.manifest.name}' exceeded storage operation limit: "
                f"{self.limits.max_storage_operations}"
            )

    def check_execution_time(self) -> None:
        """Check if execution time exceeded.

        Raises:
            SecurityError: If time limit exceeded
        """
        if self._start_time is None:
            return

        elapsed_ms = (time.time() - self._start_time) * 1000

        if elapsed_ms > self.limits.max_execution_time_ms:
            raise SecurityError(
                f"Plugin '{self.manifest.name}' exceeded execution time limit: "
                f"{self.limits.max_execution_time_ms}ms"
            )

    def check_file_size(self, size_bytes: int) -> None:
        """Check if file size is within limits.

        Args:
            size_bytes: File size in bytes

        Raises:
            SecurityError: If file too large
        """
        max_bytes = self.limits.max_file_size_mb * 1024 * 1024

        if size_bytes > max_bytes:
            raise SecurityError(f"File size {size_bytes} bytes exceeds limit: {max_bytes} bytes")

    def start_execution(self) -> None:
        """Start tracking execution time."""
        self._start_time = time.time()

    def end_execution(self) -> None:
        """End tracking execution time."""
        if self._start_time:
            elapsed_ms = (time.time() - self._start_time) * 1000
            logger.debug("Plugin '%s' execution time: %.2fms", self.manifest.name, elapsed_ms)
            self._start_time = None

    def reset_counters(self) -> None:
        """Reset resource usage counters."""
        self._network_requests = 0
        self._storage_operations = 0

    def get_stats(self) -> dict[str, Any]:
        """Get sandbox statistics.

        Returns:
            Dict with usage statistics
        """
        return {
            "plugin": self.manifest.name,
            "network_requests": self._network_requests,
            "storage_operations": self._storage_operations,
            "limits": {
                "max_network_requests": self.limits.max_network_requests,
                "max_storage_operations": self.limits.max_storage_operations,
                "max_execution_time_ms": self.limits.max_execution_time_ms,
            },
        }


class SecurePluginContext:
    """Permission-gated plugin context.

    Intentionally exposes only guarded storage/http proxies plus read-only
    plugin config.
    """

    __slots__ = ("_sandbox", "_storage_proxy", "_http_proxy", "_config")

    def __init__(
        self,
        sandbox: PluginSandbox,
        storage: Optional[Any] = None,
        http_client: Optional[Any] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        """Initialize secure context.

        Args:
            sandbox: Plugin sandbox
            storage: Database storage (optional)
            http_client: HTTP client (optional)
            config: Plugin configuration (optional)
        """
        self._sandbox = sandbox
        self._storage_proxy = SecureStorageProxy(sandbox, storage)
        self._http_proxy = SecureHTTPClientProxy(sandbox, http_client)
        self._config = config or {}

    @property
    def storage(self) -> Any:
        """Get storage with permission check."""
        self._sandbox.check_permission(Permission.STORAGE_READ)
        return self._storage_proxy

    @property
    def http_client(self) -> Any:
        """Get HTTP client with permission check."""
        self._sandbox.check_permission(Permission.NETWORK_HTTP)
        return self._http_proxy

    @property
    def config(self) -> dict[str, Any]:
        """Get plugin configuration (read-only)."""
        return self._config.copy()


class SecureStorageProxy:
    """Proxy for storage with permission checks."""

    __slots__ = ("_sandbox", "_storage")

    def __init__(self, sandbox: PluginSandbox, storage: Any):
        """Initialize proxy.

        Args:
            sandbox: Plugin sandbox
            storage: Actual storage object
        """
        self._sandbox = sandbox
        self._storage = storage

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        """Fetch one row with permission check."""
        self._sandbox.check_permission(Permission.STORAGE_READ)
        self._sandbox.check_storage_operation()
        return self._storage.fetchone(query, params)

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
        """Fetch all rows with permission check."""
        self._sandbox.check_permission(Permission.STORAGE_READ)
        self._sandbox.check_storage_operation()
        return cast(list[Any], self._storage.fetchall(query, params))

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        """Execute query with permission check."""
        self._sandbox.check_permission(Permission.STORAGE_WRITE)
        self._sandbox.check_storage_operation()
        return self._storage.execute(query, params)


class SecureHTTPClientProxy:
    """Proxy for HTTP client with permission checks."""

    __slots__ = ("_sandbox", "_client")

    def __init__(self, sandbox: PluginSandbox, client: Any):
        """Initialize proxy.

        Args:
            sandbox: Plugin sandbox
            client: Actual HTTP client
        """
        self._sandbox = sandbox
        self._client = client

    def send(self, request: Any) -> Any:
        """Send request with permission check."""
        self._sandbox.check_permission(Permission.NETWORK_HTTP)
        self._sandbox.check_network_request()
        return self._client.send(request)

    def get(self, url: str, **kwargs: Any) -> Any:
        """GET request with permission check."""
        self._sandbox.check_permission(Permission.NETWORK_HTTP)
        self._sandbox.check_network_request()
        return self._client.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        """POST request with permission check."""
        self._sandbox.check_permission(Permission.NETWORK_HTTP)
        self._sandbox.check_network_request()
        return self._client.post(url, **kwargs)


def validate_plugin_file(plugin_path: Path) -> bool:
    """Validate plugin file for security.

    Args:
        plugin_path: Path to plugin file

    Returns:
        True if valid

    Raises:
        SecurityError: If validation fails
    """
    _ensure_file_exists(plugin_path)
    _ensure_valid_extension(plugin_path)
    _ensure_valid_size(plugin_path)
    tree = _parse_plugin_ast(plugin_path)
    _validate_ast_security(plugin_path, tree)
    return True


def _ensure_file_exists(plugin_path: Path) -> None:
    if not plugin_path.exists():
        raise SecurityError(f"Plugin file not found: {plugin_path}")


def _ensure_valid_extension(plugin_path: Path) -> None:
    if plugin_path.suffix != ".py":
        raise SecurityError(f"Invalid plugin file type: {plugin_path.suffix}")


def _ensure_valid_size(plugin_path: Path) -> None:
    size = plugin_path.stat().st_size
    if size > 1024 * 1024:
        raise SecurityError(f"Plugin file too large: {size} bytes")


def _parse_plugin_ast(plugin_path: Path) -> "ast.AST":
    try:
        content = plugin_path.read_text(encoding="utf-8")
        return ast.parse(content, str(plugin_path))
    except SyntaxError as e:
        raise SecurityError(f"Plugin has syntax errors: {e}")
    except Exception as e:
        raise SecurityError(f"Failed to read plugin file: {e}")


def _validate_ast_security(plugin_path: Path, tree: "ast.AST") -> None:
    violations = []
    violations.extend(_find_dangerous_imports(tree))
    violations.extend(_find_dangerous_os_calls(tree))
    violations.extend(_find_dangerous_functions(tree))

    if violations:
        audit_logger.log_security_violation(
            "plugin_validation",
            {"plugin": str(plugin_path), "violations": violations},
        )
        detail = "; ".join(violations[:5])
        raise SecurityError(f"Plugin failed security validation: {detail}")


def _find_dangerous_imports(tree: "ast.AST") -> list[str]:
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _DANGEROUS_MODULES:
                    violations.append(f"Forbidden import: {alias.name} (line {node.lineno})")

        if isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in _DANGEROUS_MODULES:
                violations.append(f"Forbidden import: {node.module} (line {node.lineno})")

    return violations


def _find_dangerous_os_calls(tree: "ast.AST") -> list[str]:
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                if node.attr in _OS_FORBIDDEN:
                    violations.append(f"Forbidden call: os.{node.attr} (line {node.lineno})")
    return violations


def _find_dangerous_functions(tree: "ast.AST") -> list[str]:
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _DANGEROUS_FUNCTIONS:
                violations.append(f"Forbidden function: {func.id}() (line {node.lineno})")
    return violations


def _is_within(root: Path, candidate: Path) -> bool:
    """Return True when *candidate* resolves under *root* (inclusive)."""
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_import_target(base: Path) -> list[Path]:
    """Return potential .py targets for a module base path."""
    return [base.with_suffix(".py"), base / "__init__.py"]


def _iter_package_python_files(package_dir: Path, plugin_root: Path) -> list[Path]:
    """Return Python files beneath *package_dir* constrained to *plugin_root*."""
    if (
        not package_dir.exists()
        or not package_dir.is_dir()
        or not _is_within(plugin_root, package_dir)
    ):
        return []

    files: list[Path] = []
    for path in package_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if _is_within(plugin_root, path):
            files.append(path)
    return files


def _resolve_absolute_import_chain(module_name: str, plugin_root: Path) -> list[Path]:
    """Resolve package/module files touched by an absolute import chain."""
    targets: list[Path] = []
    parts = [part for part in module_name.split(".") if part]
    if not parts:
        return targets

    current = plugin_root
    for index, part in enumerate(parts):
        current = current / part
        candidates = _resolve_import_target(current)
        for candidate in candidates:
            if candidate.exists() and _is_within(plugin_root, candidate):
                targets.append(candidate)
        # Stop descending if the current segment is clearly not a local package.
        if not (current.is_dir() or current.with_suffix(".py").exists()):
            break

    return targets


def _iter_local_import_targets(node: ast.AST, current_file: Path, plugin_root: Path) -> list[Path]:
    """Resolve local import candidates for *node* limited to *plugin_root*."""
    targets: list[Path] = []

    if isinstance(node, ast.Import):
        for alias in node.names:
            targets.extend(_resolve_absolute_import_chain(alias.name, plugin_root))
        return [p for p in targets if p.exists() and _is_within(plugin_root, p)]

    if not isinstance(node, ast.ImportFrom):
        return []

    # Relative import: from .x import y / from ..pkg import z
    if node.level and node.level > 0:
        anchor = current_file.parent
        for _ in range(node.level - 1):
            anchor = anchor.parent
        if node.module:
            anchor = anchor.joinpath(*node.module.split("."))

        targets.extend(_resolve_import_target(anchor))
        for alias in node.names:
            if alias.name == "*":
                targets.extend(_iter_package_python_files(anchor, plugin_root))
                continue
            targets.extend(_resolve_import_target(anchor / alias.name))
        return [p for p in targets if p.exists() and _is_within(plugin_root, p)]

    # Absolute import-from: from pkg.mod import x
    if node.module:
        anchor = plugin_root.joinpath(*node.module.split("."))
        targets.extend(_resolve_import_target(anchor))
        for alias in node.names:
            if alias.name == "*":
                targets.extend(_iter_package_python_files(anchor, plugin_root))
                continue
            targets.extend(_resolve_import_target(anchor / alias.name))

    return [p for p in targets if p.exists() and _is_within(plugin_root, p)]


def validate_plugin_dependency_graph(
    entry_file: Path, plugin_root: Optional[Path] = None
) -> set[Path]:
    """Validate *entry_file* and all locally imported plugin modules.

    Traverses local Python imports reachable from the plugin entry point and
    applies ``validate_plugin_file`` to every discovered module before runtime
    loading. This blocks bypasses where a clean entry file imports unsafe code
    from neighboring files.
    """
    root = (plugin_root or entry_file.parent).resolve()
    to_visit: list[Path] = [entry_file.resolve()]
    visited: set[Path] = set()

    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue
        if not _is_within(root, current):
            raise SecurityError(f"Plugin import escapes plugin directory: {current}")

        validate_plugin_file(current)
        visited.add(current)

        try:
            source = current.read_text(encoding="utf-8")
            tree = ast.parse(source, str(current))
        except SyntaxError as exc:
            raise SecurityError(f"Plugin has syntax errors: {exc}")
        except Exception as exc:
            raise SecurityError(f"Failed to inspect plugin dependency '{current}': {exc}")

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in _iter_local_import_targets(node, current, root):
                    if target not in visited:
                        to_visit.append(target)

    return visited


def calculate_checksum(plugin_path: Path) -> str:
    """Calculate plugin file checksum.

    Args:
        plugin_path: Path to plugin file

    Returns:
        SHA256 checksum
    """
    sha256 = hashlib.sha256()

    with open(plugin_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


def verify_checksum(plugin_path: Path, expected_checksum: str) -> bool:
    """Verify plugin checksum.

    Args:
        plugin_path: Path to plugin file
        expected_checksum: Expected checksum

    Returns:
        True if checksums match

    Raises:
        SecurityError: If checksums don't match
    """
    actual = calculate_checksum(plugin_path)

    if actual != expected_checksum:
        raise SecurityError(
            f"Plugin checksum mismatch. Expected: {expected_checksum}, Got: {actual}"
        )

    return True
