"""Plugin security and sandboxing.

This module provides security features for plugins including:
- Permission system
- Resource limits
- Execution sandboxing
- Security validation
"""

import os
import sys
import time
import hashlib
import logging
from enum import Enum
from typing import Set, Optional, Dict, Any, List
from pathlib import Path
from dataclasses import dataclass, field

from equinox.core.exceptions import SecurityError, PluginError
from equinox.core.audit import get_audit_logger, AuditEventType

logger = logging.getLogger(__name__)
audit_logger = get_audit_logger()


class Permission(Enum):
    """Plugin permissions."""

    # Network permissions
    NETWORK_HTTP = "network.http"           # Make HTTP requests
    NETWORK_HTTPS = "network.https"         # Make HTTPS requests
    NETWORK_WEBSOCKET = "network.websocket" # WebSocket connections

    # File system permissions
    FILE_READ = "file.read"                 # Read files
    FILE_WRITE = "file.write"               # Write files
    FILE_DELETE = "file.delete"             # Delete files

    # Storage permissions
    STORAGE_READ = "storage.read"           # Read from database
    STORAGE_WRITE = "storage.write"         # Write to database
    STORAGE_DELETE = "storage.delete"       # Delete from database

    # Credential permissions
    CREDENTIAL_READ = "credential.read"     # Read credentials
    CREDENTIAL_WRITE = "credential.write"   # Write credentials

    # System permissions
    SYSTEM_EXECUTE = "system.execute"       # Execute system commands
    SYSTEM_ENV = "system.env"               # Access environment variables

    # Request/Response permissions
    REQUEST_MODIFY = "request.modify"       # Modify outgoing requests
    RESPONSE_MODIFY = "response.modify"     # Modify incoming responses


@dataclass
class PluginManifest:
    """Plugin manifest with metadata and permissions."""

    name: str
    version: str
    author: str
    description: str = ""
    permissions: Set[Permission] = field(default_factory=set)
    homepage: str = ""
    license: str = ""
    checksum: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginManifest":
        """Create manifest from dictionary.

        Args:
            data: Manifest data

        Returns:
            PluginManifest instance

        Raises:
            ValidationError: If manifest is invalid
        """
        # Convert permission strings to Permission enum
        permissions = set()
        for perm_str in data.get("permissions", []):
            try:
                perm = Permission(perm_str)
                permissions.add(perm)
            except ValueError:
                logger.warning("Unknown permission: %s", perm_str)

        return cls(
            name=data["name"],
            version=data["version"],
            author=data["author"],
            description=data.get("description", ""),
            permissions=permissions,
            homepage=data.get("homepage", ""),
            license=data.get("license", ""),
            checksum=data.get("checksum")
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert manifest to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "permissions": [p.value for p in self.permissions],
            "homepage": self.homepage,
            "license": self.license,
            "checksum": self.checksum
        }


@dataclass
class ResourceLimits:
    """Resource limits for plugin execution."""

    max_memory_mb: int = 100                 # Maximum memory usage in MB
    max_execution_time_ms: int = 5000        # Maximum execution time in ms
    max_file_size_mb: int = 10               # Maximum file size to read/write
    max_network_requests: int = 100          # Maximum network requests
    max_storage_operations: int = 100        # Maximum database operations

    def __post_init__(self):
        """Validate limits."""
        if self.max_memory_mb < 1 or self.max_memory_mb > 1000:
            raise ValueError("max_memory_mb must be between 1 and 1000")

        if self.max_execution_time_ms < 100 or self.max_execution_time_ms > 60000:
            raise ValueError("max_execution_time_ms must be between 100 and 60000")


class PluginSandbox:
    """Sandbox for plugin execution with security controls."""

    def __init__(
        self,
        manifest: PluginManifest,
        limits: Optional[ResourceLimits] = None
    ):
        """Initialize plugin sandbox.

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
        audit_logger.log_plugin_event(
            manifest.name,
            "loaded",
            user="system"
        )

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
                {
                    "plugin": self.manifest.name,
                    "permission": permission.value,
                    "action": "denied"
                }
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
            raise SecurityError(
                f"File size {size_bytes} bytes exceeds limit: {max_bytes} bytes"
            )

    def start_execution(self) -> None:
        """Start tracking execution time."""
        self._start_time = time.time()

    def end_execution(self) -> None:
        """End tracking execution time."""
        if self._start_time:
            elapsed_ms = (time.time() - self._start_time) * 1000
            logger.debug(
                "Plugin '%s' execution time: %.2fms", self.manifest.name, elapsed_ms
            )
            self._start_time = None

    def reset_counters(self) -> None:
        """Reset resource usage counters."""
        self._network_requests = 0
        self._storage_operations = 0

    def get_stats(self) -> Dict[str, Any]:
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
                "max_execution_time_ms": self.limits.max_execution_time_ms
            }
        }


class SecurePluginContext:
    """Secure plugin context with permission checks."""

    def __init__(
        self,
        sandbox: PluginSandbox,
        storage: Optional[Any] = None,
        http_client: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        """Initialize secure context.

        Args:
            sandbox: Plugin sandbox
            storage: Database storage (optional)
            http_client: HTTP client (optional)
            config: Plugin configuration (optional)
        """
        self._sandbox = sandbox
        self._storage = storage
        self._http_client = http_client
        self._config = config or {}

    @property
    def storage(self) -> Any:
        """Get storage with permission check."""
        self._sandbox.check_permission(Permission.STORAGE_READ)
        return SecureStorageProxy(self._sandbox, self._storage)

    @property
    def http_client(self) -> Any:
        """Get HTTP client with permission check."""
        self._sandbox.check_permission(Permission.NETWORK_HTTP)
        return SecureHTTPClientProxy(self._sandbox, self._http_client)

    @property
    def config(self) -> Dict[str, Any]:
        """Get plugin configuration (read-only)."""
        return self._config.copy()


class SecureStorageProxy:
    """Proxy for storage with permission checks."""

    def __init__(self, sandbox: PluginSandbox, storage: Any):
        """Initialize proxy.

        Args:
            sandbox: Plugin sandbox
            storage: Actual storage object
        """
        self._sandbox = sandbox
        self._storage = storage

    def fetchone(self, query: str, params: tuple = ()) -> Any:
        """Fetch one row with permission check."""
        self._sandbox.check_permission(Permission.STORAGE_READ)
        self._sandbox.check_storage_operation()
        return self._storage.fetchone(query, params)

    def fetchall(self, query: str, params: tuple = ()) -> List[Any]:
        """Fetch all rows with permission check."""
        self._sandbox.check_permission(Permission.STORAGE_READ)
        self._sandbox.check_storage_operation()
        return self._storage.fetchall(query, params)

    def execute(self, query: str, params: tuple = ()) -> Any:
        """Execute query with permission check."""
        self._sandbox.check_permission(Permission.STORAGE_WRITE)
        self._sandbox.check_storage_operation()
        return self._storage.execute(query, params)


class SecureHTTPClientProxy:
    """Proxy for HTTP client with permission checks."""

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

    def get(self, url: str, **kwargs) -> Any:
        """GET request with permission check."""
        self._sandbox.check_permission(Permission.NETWORK_HTTP)
        self._sandbox.check_network_request()
        return self._client.get(url, **kwargs)

    def post(self, url: str, **kwargs) -> Any:
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
    # Check file exists
    if not plugin_path.exists():
        raise SecurityError(f"Plugin file not found: {plugin_path}")

    # Check file extension
    if plugin_path.suffix != ".py":
        raise SecurityError(f"Invalid plugin file type: {plugin_path.suffix}")

    # Check file size (max 1MB for plugin)
    size = plugin_path.stat().st_size
    if size > 1024 * 1024:
        raise SecurityError(f"Plugin file too large: {size} bytes")

    # Check for suspicious imports via AST analysis
    try:
        content = plugin_path.read_text(encoding="utf-8")
        tree = __import__("ast").parse(content, str(plugin_path))
    except SyntaxError as e:
        raise SecurityError(f"Plugin has syntax errors: {e}")
    except Exception as e:
        raise SecurityError(f"Failed to read plugin file: {e}")

    _DANGEROUS_MODULES = frozenset({
        "subprocess", "shutil", "ctypes", "multiprocessing",
        "signal", "resource", "pty", "fcntl", "termios",
        # Additional modules that can bypass sandbox restrictions:
        "importlib",    # dynamic import bypasses AST checks
        "code",         # interactive interpreter
        "codeop",       # compile helpers
        "dis",          # bytecode disassembly / introspection
        "inspect",      # frame/source introspection
        "gc",           # garbage collector — gc.get_objects() leaks all live objects
        "_thread",      # low-level thread API
        "socket",       # raw network access outside HTTP proxy
        "http",         # direct HTTP client/server
        "xmlrpc",       # XML-RPC client/server
        "pickle",       # arbitrary code execution via deserialization
        "shelve",       # uses pickle internally
        "marshal",      # bytecode (de)serialization
        "builtins",     # full builtins access
        "io",           # raw file I/O bypasses sandbox file checks
        "webbrowser",   # can open arbitrary URLs / commands
        "zipimport",    # import from zips — bypass AST validation
        "runpy",        # run modules — bypass AST validation
    })

    _DANGEROUS_FUNCTIONS = frozenset({
        "eval", "exec", "compile", "__import__", "breakpoint",
    })

    import ast as _ast

    violations: list = []

    for node in _ast.walk(tree):
        # Block dangerous imports
        if isinstance(node, (_ast.Import, _ast.ImportFrom)):
            names = []
            if isinstance(node, _ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in _DANGEROUS_MODULES:
                    violations.append(f"Forbidden import: {node.module} (line {node.lineno})")
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in _DANGEROUS_MODULES:
                        violations.append(f"Forbidden import: {alias.name} (line {node.lineno})")

        # Block os.system / os.popen etc.
        if isinstance(node, _ast.Attribute):
            if isinstance(node.value, _ast.Name) and node.value.id == "os":
                if node.attr in ("system", "popen", "exec", "execv", "execve",
                                 "spawn", "spawnl", "spawnle", "fork"):
                    violations.append(
                        f"Forbidden call: os.{node.attr} (line {node.lineno})"
                    )

        # Block bare eval/exec/compile calls
        if isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name) and func.id in _DANGEROUS_FUNCTIONS:
                violations.append(
                    f"Forbidden function: {func.id}() (line {node.lineno})"
                )

    if violations:
        detail = "; ".join(violations[:5])
        audit_logger.log_security_violation(
            "plugin_validation",
            {"plugin": str(plugin_path), "violations": violations},
        )
        raise SecurityError(f"Plugin failed security validation: {detail}")

    return True


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
