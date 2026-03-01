# Equinox

A **secure, local-first API testing tool** with both CLI and GUI interfaces - your lightweight, customizable alternative to Postman.

## Features

### Core Functionality
- **Local-First**: All data stored locally in SQLite - no cloud required
- **CLI & GUI**: Use command-line for automation or GUI for visual exploration
- **Collections**: Organize requests into collections
- **Environments**: Manage multiple environments with variables
- **History**: Track all requests and responses
- **Plugin System**: Extend functionality with custom plugins
- **Response Visualization**: View JSON, XML, HTML responses with syntax highlighting
- **Import/Export**: Share collections and environments

### Security Features 🔒

- **Zero-Trust Input Validation**: All inputs validated against injection attacks
- **Secure Credential Storage**: AES-256 encrypted credential storage
- **SQL Injection Prevention**: Parameterized queries throughout
- **Rate Limiting**: Configurable request rate limits
- **SSL/TLS Verification**: Certificate validation with custom CA support
- **Audit Logging**: Comprehensive security event logging
- **No Plaintext Secrets**: All sensitive data encrypted at rest
- **Path Traversal Protection**: File system access controls
- **CRLF Injection Prevention**: Header validation
- **Command Injection Protection**: Input sanitization

### Authentication Support
- OAuth2 with automatic token refresh
- Bearer Token
- API Key (header or query parameter)
- Basic Authentication

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/equinox.git
cd equinox

# Install dependencies
pip install -e .

# For development (includes security tools)
pip install -e ".[dev]"
```

## Quick Start

### CLI Usage

```bash
# Send a GET request
equinox get https://api.example.com/users

# Send a POST request with JSON body
equinox post https://api.example.com/users --json '{"name": "John"}'

# Use authentication
equinox get https://api.example.com/protected --auth bearer:YOUR_TOKEN

# Save request to collection
equinox save my-request --url https://api.example.com/users --method GET

# View history
equinox history
```

### GUI Usage

```bash
# Launch GUI
equinox gui
```

### Programmatic Usage

```python
from equinox.core.client import HTTPClient
from equinox.core.request import Request
from equinox.core.secure_storage import SecureStorage
from equinox.auth import BearerAuth

# Secure credential storage
storage = SecureStorage()
storage.store("api_token", "your-secret-token")

# Create secure client
client = HTTPClient(
    timeout=30.0,
    verify_ssl=True,
    max_rate_per_minute=60
)

# Make authenticated request
request = Request(
    method="GET",
    url="https://api.example.com/users",
    auth=BearerAuth(storage.retrieve("api_token"))
)

response = client.send(request)
print(f"Status: {response.status_code}")
print(f"Body: {response.json()}")
```

## Security

Equinox follows **zero-trust security principles**:

- ✅ All inputs validated before processing
- ✅ Credentials encrypted with AES-256
- ✅ Parameterized database queries
- ✅ Rate limiting and timeout controls
- ✅ Comprehensive audit logging
- ✅ Security testing in CI/CD
- ✅ Regular dependency scanning

See [docs/SECURITY.md](docs/SECURITY.md) for detailed security information.

## Project Structure

```
equinox/
├── src/equinox/
│   ├── core/              # Core HTTP client, validation, security
│   │   ├── client.py      # Secure HTTP client
│   │   ├── validation.py  # Input validation
│   │   ├── secure_storage.py  # Encrypted credential storage
│   │   └── audit.py       # Security audit logging
│   ├── auth/              # Authentication strategies
│   ├── storage/           # SQLite database and persistence
│   ├── cli/               # Command-line interface
│   ├── gui/               # PyQt6 GUI application
│   └── plugins/           # Plugin system
├── tests/                 # Comprehensive test suite
│   ├── test_validation.py     # Validation tests
│   ├── test_security.py       # Security tests
│   └── test_secure_storage.py # Credential storage tests
├── docs/                  # Documentation
│   ├── SECURITY.md        # Security policy
│   └── getting-started.md # Getting started guide
└── .github/workflows/     # CI/CD with security scanning
```

## Development

### Setup Development Environment

```bash
# Install development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Run tests
pytest

# Run security checks
bandit -r src/ -ll
safety check

# Run all pre-commit checks
pre-commit run --all-files
```

### Testing

```bash
# Run all tests with coverage
pytest --cov=equinox --cov-report=html

# Run security tests
pytest tests/test_security.py -v

# Run specific test file
pytest tests/test_validation.py -v
```

### Code Quality

```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Lint code
flake8 src/ tests/

# Type checking
mypy src/
```

## CI/CD Pipeline

The project includes automated security scanning:

- **Bandit**: Static Application Security Testing (SAST)
- **Safety**: Dependency vulnerability scanning
- **pytest**: Comprehensive test suite with security tests
- **MyPy**: Type checking
- **Code Coverage**: >80% coverage enforced

## Plugin Development

See [docs/plugin-development.md](docs/plugin-development.md) for creating custom plugins.

Example plugin:

```python
from equinox.plugins.base import Plugin

class MyPlugin(Plugin):
    def on_request(self, request):
        # Modify request before sending
        request.headers['X-Custom'] = 'value'
        return request

    def on_response(self, response):
        # Process response
        print(f"Response: {response.status_code}")
        return response
```

## Security Reporting

If you discover a security vulnerability, please email security@equinox-project.org or create a private security advisory on GitHub.

**Do not** publicly disclose security vulnerabilities.

## Roadmap

### Completed ✅
- Core HTTP client with full REST support
- Secure credential storage with encryption
- Input validation and sanitization
- SQL injection prevention
- Rate limiting and timeout controls
- Audit logging
- CI/CD with security scanning
- Comprehensive test suite

### Planned 📋
- Plugin security sandbox
- WebSocket support
- GraphQL query builder
- Postman collection import
- Mock server functionality
- Code generation from requests
- Dark mode/theming
- Request chaining with variable extraction

## Shell Completion

Equinox supports automatic shell completion via Click:

```bash
# Bash — add to ~/.bashrc
eval "$(_EQUINOX_COMPLETE=bash_source equinox)"

# Zsh — add to ~/.zshrc
eval "$(_EQUINOX_COMPLETE=zsh_source equinox)"

# Fish — add to ~/.config/fish/completions/equinox.fish
_EQUINOX_COMPLETE=fish_source equinox | source

# PowerShell — add to $PROFILE
_EQUINOX_COMPLETE=powershell_source equinox | Invoke-Expression
```

## License

MIT License - see [LICENSE](LICENSE)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

## Acknowledgments

- Built with [httpx](https://www.python-httpx.org/)
- GUI powered by [PyQt6](https://www.riverbankcomputing.com/software/pyqt/)
- Security by design following [OWASP](https://owasp.org/) guidelines

---

**Made with ❤️ and 🔒 by the Equinox Team**
