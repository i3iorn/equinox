# Plugin Development Guide

Equinox supports plugins to extend functionality. This guide will help you create custom plugins.

## Plugin Structure

A plugin is a directory containing:

```
my-plugin/
├── manifest.json          # Plugin metadata
└── plugin.py             # Plugin implementation
```

### manifest.json

```json
{
  "name": "my-plugin",
  "displayName": "My Plugin",
  "version": "1.0.0",
  "description": "A sample plugin",
  "author": "Your Name",
  "main": "plugin.py",
  "permissions": []
}
```

### plugin.py

```python
from equinox.plugins import Plugin, PluginContext
from equinox.core.request import Request, Response

class PluginClass(Plugin):
    """Your plugin implementation"""

    @property
    def name(self) -> str:
        return "my-plugin"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "A sample plugin"

    def activate(self):
        """Called when plugin is loaded"""
        print(f"{self.name} activated!")

    def on_request(self, request: Request):
        """Called before sending request"""
        print(f"Sending {request.method} to {request.url}")
        # Modify request if needed
        return None  # Return None to use original request

    def on_response(self, request: Request, response: Response):
        """Called after receiving response"""
        print(f"Received {response.status_code}")
        # Modify response if needed
        return None  # Return None to use original response

    def on_error(self, request: Request, error: Exception):
        """Called when request fails"""
        print(f"Request failed: {error}")
```

## Plugin API

### Plugin Base Class

All plugins must inherit from `Plugin` and implement:

- `name` (property): Plugin name
- `version` (property): Plugin version
- `description` (property): Plugin description (optional)

### Lifecycle Methods

#### activate()

Called when plugin is loaded. Use for initialization.

```python
def activate(self):
    self.logger = setup_logger()
    self.config = load_config()
```

#### deactivate()

Called when plugin is unloaded. Use for cleanup.

```python
def deactivate(self):
    self.logger.close()
    save_state()
```

### Hook Methods

#### on_request(request: Request) -> Optional[Request]

Called before request is sent. Can modify the request.

```python
def on_request(self, request: Request):
    # Add custom header
    request.headers["X-Custom-Header"] = "value"
    return request
```

Return `None` to use the original request, or return a modified `Request` object.

#### on_response(request: Request, response: Response) -> Optional[Response]

Called after response is received. Can modify the response.

```python
def on_response(self, request: Request, response: Response):
    # Log response time
    self.logger.info(f"Response time: {response.elapsed}s")
    return None  # Use original response
```

#### on_error(request: Request, error: Exception)

Called when request fails.

```python
def on_error(self, request: Request, error: Exception):
    self.logger.error(f"Request to {request.url} failed: {error}")
```

## Plugin Context

Plugins receive a `PluginContext` object with:

- `storage`: Access to database storage
- `http_client`: HTTP client instance
- `config`: Plugin configuration

```python
def activate(self):
    # Access storage
    db = self.context.storage

    # Access HTTP client
    client = self.context.http_client

    # Access config
    settings = self.context.config
```

## Example Plugins

### Logger Plugin

Log all requests and responses:

```python
from equinox.plugins import Plugin
from equinox.core.request import Request, Response
import logging

class PluginClass(Plugin):
    @property
    def name(self) -> str:
        return "logger"

    @property
    def version(self) -> str:
        return "1.0.0"

    def activate(self):
        logging.basicConfig(
            filename="equinox-requests.log",
            level=logging.INFO,
            format="%(asctime)s - %(message)s"
        )

    def on_request(self, request: Request):
        logging.info(f"→ {request.method} {request.url}")
        return None

    def on_response(self, request: Request, response: Response):
        logging.info(f"← {response.status_code} ({response.elapsed:.3f}s)")
        return None
```

### Authentication Plugin

Automatically add authentication:

```python
from equinox.plugins import Plugin
from equinox.core.request import Request

class PluginClass(Plugin):
    @property
    def name(self) -> str:
        return "auto-auth"

    @property
    def version(self) -> str:
        return "1.0.0"

    def activate(self):
        self.api_key = "your-api-key-here"

    def on_request(self, request: Request):
        # Add API key to all requests
        request.headers["X-API-Key"] = self.api_key
        return request
```

### Retry Plugin

Automatically retry failed requests:

```python
from equinox.plugins import Plugin
from equinox.core.request import Request, Response
import time

class PluginClass(Plugin):
    @property
    def name(self) -> str:
        return "retry"

    @property
    def version(self) -> str:
        return "1.0.0"

    def activate(self):
        self.max_retries = 3
        self.retry_delay = 1.0

    def on_error(self, request: Request, error: Exception):
        # Retry logic would go here
        print(f"Request failed, retrying...")
```

## Installing Plugins

1. Create plugin directory: `~/.equinox/plugins/my-plugin/`
2. Add `manifest.json` and `plugin.py`
3. Restart Equinox

Plugins are automatically loaded on startup.

## Best Practices

1. **Error Handling**: Always handle errors gracefully
2. **Performance**: Avoid blocking operations in hooks
3. **Security**: Validate and sanitize user input
4. **Documentation**: Document your plugin's purpose and configuration
5. **Testing**: Test plugins with various requests and scenarios

## Debugging

Enable debug mode to see plugin loading messages:

```bash
equinox --debug gui
```

Plugin errors are printed to console.
