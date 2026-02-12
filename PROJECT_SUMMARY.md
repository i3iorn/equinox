# Equinox Project Summary

## Overview

**Equinox** is a local-first API testing tool that combines concepts from multiple API-related projects to create a streamlined, customizable alternative to Postman. It features both a command-line interface (CLI) and a graphical user interface (GUI) built with PyQt6.

## Project Origins

Equinox was created by consolidating ideas and concepts from several existing API projects:

- **api_essentials**: Core API client library, authentication strategies, endpoint system
- **api_viewer**: PyQt6 UI framework, SQLite storage, worker threads for async operations
- **api_explorer**: API specification viewing and wxPython GUI patterns
- **api_tools**: Plugin architecture and extensibility framework
- **api_client**: Simple integration patterns and client design
- **api_auth**: Authentication strategies (OAuth2, Bearer, Basic, API Key)

## Architecture

### Core Components

1. **Core HTTP Client** (`src/equinox/core/`)
   - `client.py`: HTTPClient using httpx for making requests
   - `request.py`: Request and Response models
   - `exceptions.py`: Custom exception classes

2. **Authentication** (`src/equinox/auth/`)
   - `bearer.py`: Bearer token authentication
   - `api_key.py`: API key authentication (header or query)
   - `basic.py`: Basic HTTP authentication
   - `oauth2.py`: OAuth2 with token refresh

3. **Storage Layer** (`src/equinox/storage/`)
   - `database.py`: SQLite database manager
   - `collections.py`: Collection management
   - `environments.py`: Environment variables
   - `history.py`: Request/response history
   - `schema.sql`: Database schema

4. **CLI** (`src/equinox/cli/`)
   - `main.py`: Click-based command-line interface
   - Commands: get, post, put, patch, delete, collection, env, history, gui

5. **GUI** (`src/equinox/gui/`)
   - `app.py`: PyQt6 application entry point
   - `window.py`: Main window layout
   - `request_panel.py`: Request builder with tabs
   - `response_panel.py`: Response viewer
   - `collections_panel.py`: Collections tree view
   - `history_panel.py`: History list view

6. **Plugin System** (`src/equinox/plugins/`)
   - `base.py`: Plugin base classes
   - `manager.py`: Plugin loading and lifecycle management

## Key Features

### ✅ Implemented Features

1. **Full REST API Support**
   - GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS
   - Custom headers and query parameters
   - Request body support (JSON, XML, plain text)

2. **Multiple Authentication Methods**
   - Bearer token
   - API key (header or query parameter)
   - Basic authentication
   - OAuth2 with automatic token refresh

3. **Local-First Storage**
   - SQLite database at `~/.equinox/equinox.db`
   - No cloud dependencies
   - Full data ownership

4. **Collections**
   - Organize requests into collections
   - Save and load requests
   - Collection management (create, delete, update)

5. **Environments**
   - Multiple environment support
   - Variable interpolation with `{{VARIABLE}}`
   - Active environment selection

6. **History Tracking**
   - Automatic tracking of all requests
   - Success/failure statistics
   - Replay previous requests

7. **Dual Interface**
   - CLI for automation and scripting
   - GUI for visual exploration

8. **Plugin System**
   - Extensible architecture
   - Lifecycle hooks (activate, deactivate)
   - Request/response transformation
   - Example logger plugin included

9. **Response Handling**
   - JSON syntax highlighting
   - Response time tracking
   - Size calculation
   - Header inspection

10. **Additional Features**
    - Curl command generation
    - Context menus for quick actions
    - Background request execution
    - Error handling and logging

## Project Structure

```
equinox/
├── src/equinox/
│   ├── __init__.py
│   ├── core/                 # Core HTTP client
│   │   ├── client.py
│   │   ├── request.py
│   │   └── exceptions.py
│   ├── auth/                 # Authentication strategies
│   │   ├── bearer.py
│   │   ├── api_key.py
│   │   ├── basic.py
│   │   └── oauth2.py
│   ├── storage/              # SQLite storage
│   │   ├── database.py
│   │   ├── collections.py
│   │   ├── environments.py
│   │   ├── history.py
│   │   └── schema.sql
│   ├── cli/                  # Command-line interface
│   │   └── main.py
│   ├── gui/                  # PyQt6 GUI
│   │   ├── app.py
│   │   ├── window.py
│   │   ├── request_panel.py
│   │   ├── response_panel.py
│   │   ├── collections_panel.py
│   │   └── history_panel.py
│   └── plugins/              # Plugin system
│       ├── base.py
│       └── manager.py
├── tests/                    # Test suite
│   └── test_client.py
├── docs/                     # Documentation
│   ├── getting-started.md
│   └── plugin-development.md
├── examples/                 # Examples
│   ├── simple_request.py
│   ├── sample-collection.json
│   ├── plugins/
│   │   └── logger/
│   └── environments/
├── setup.py                  # Package setup
├── pyproject.toml           # Build configuration
├── requirements.txt         # Dependencies
├── README.md                # Main documentation
├── CHANGELOG.md             # Version history
├── CONTRIBUTING.md          # Contribution guide
└── LICENSE                  # MIT License
```

## Installation

```bash
cd equinox
pip install -e .
```

For development:
```bash
pip install -e ".[dev]"
```

## Usage Examples

### CLI

```bash
# Send GET request
equinox get https://api.github.com/users/octocat

# Send POST with JSON
equinox post https://httpbin.org/post --json '{"name": "John"}'

# With authentication
equinox get https://api.example.com/protected --auth bearer:TOKEN

# Save to collection
equinox get https://api.example.com/users --save "List Users"

# View history
equinox history
```

### GUI

```bash
equinox gui
```

### Programmatic

```python
from equinox import HTTPClient, Request
from equinox.auth import BearerAuth

client = HTTPClient()
request = Request(
    method="GET",
    url="https://api.example.com/users",
    auth=BearerAuth("your-token")
)
response = client.send(request)

print(f"Status: {response.status_code}")
print(f"Body: {response.json()}")
```

## Technologies Used

- **Python 3.9+**: Core language
- **httpx**: Modern HTTP client
- **PyQt6**: GUI framework
- **SQLite**: Local database
- **Click**: CLI framework
- **Pygments**: Syntax highlighting

## Future Enhancements

Potential areas for improvement:

1. **WebSocket Support**: Real-time connections
2. **GraphQL**: Query builder and schema introspection
3. **Import/Export**: Postman collection import
4. **Code Generation**: Generate code from requests
5. **Mock Server**: Built-in mock server
6. **Collaboration**: Share collections (optional)
7. **Themes**: Dark mode and custom themes
8. **Request Chaining**: Use response data in next request
9. **Advanced Auth**: AWS Signature, Digest Auth
10. **Performance Testing**: Load testing capabilities

## Statistics

- **33 Python files** created
- **8 major components** (core, auth, storage, CLI, GUI, plugins, docs, examples)
- **4 authentication methods** supported
- **7 HTTP methods** supported
- **Local-first** architecture with SQLite
- **Extensible** via plugin system

## Comparison with Existing Projects

### api_essentials
- ✅ Adopted: Core client architecture, auth strategies
- ✅ Improved: Simplified API, better error handling

### api_viewer
- ✅ Adopted: PyQt6 GUI, SQLite storage
- ✅ Improved: Modern UI design, better organization

### api_explorer
- ✅ Adopted: Specification viewing concepts
- 📋 Future: Full OpenAPI spec support

### api_tools
- ✅ Adopted: Plugin architecture
- ✅ Improved: Simpler manifest, better hooks

### api_client
- ✅ Adopted: Simple integration patterns
- ✅ Improved: More flexible, better abstractions

## Development

```bash
# Run tests
pytest

# Format code
black src/ tests/

# Type checking
mypy src/

# Run example
python examples/simple_request.py
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License - see [LICENSE](LICENSE)

## Conclusion

Equinox successfully combines the best concepts from multiple API-related projects into a cohesive, local-first API testing tool. It provides both CLI and GUI interfaces, supports multiple authentication methods, includes a plugin system for extensibility, and maintains all data locally in SQLite.

The project is well-structured, documented, and ready for further development and customization.
