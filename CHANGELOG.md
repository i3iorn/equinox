# Changelog

All notable changes to Equinox will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-02-12

### Added
- Initial release of Equinox
- Core HTTP client with support for all HTTP methods (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS)
- Authentication strategies (Bearer, API Key, Basic Auth, OAuth2)
- SQLite-based local storage for requests, responses, collections, and environments
- Command-line interface (CLI) for sending requests and managing collections
- PyQt6-based GUI application with:
  - Request builder panel
  - Response viewer with syntax highlighting
  - Collections management
  - History tracking
  - Environment variable support
- Plugin system for extensibility:
  - Base plugin architecture
  - Plugin manager with lifecycle hooks
  - Request/response transformation hooks
  - Example logger plugin
- Documentation:
  - Getting started guide
  - Plugin development guide
  - Example collections and environments
- Request history with automatic tracking
- Environment variables with {{variable}} interpolation
- Response time tracking
- Curl command generation from requests
- Request/response storage in SQLite database
- Context menus for collection and request management
- Multiple authentication methods
- JSON response formatting
- Local-first architecture with no cloud dependencies

### Features
- ✅ Full REST API support (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS)
- ✅ Multiple authentication methods
- ✅ Request collections for organization
- ✅ Environment variables for different configurations
- ✅ Request history tracking
- ✅ CLI and GUI interfaces
- ✅ Plugin system for extensibility
- ✅ Local SQLite database storage
- ✅ Response syntax highlighting
- ✅ Curl command export

[0.1.0]: https://github.com/yourusername/equinox/releases/tag/v0.1.0
