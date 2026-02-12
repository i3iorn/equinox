# Contributing to Equinox

Thank you for your interest in contributing to Equinox! This document provides guidelines and instructions for contributing.

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/yourusername/equinox.git
   cd equinox
   ```
3. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
4. Install in development mode:
   ```bash
   pip install -e ".[dev]"
   ```

## Development Workflow

1. Create a new branch for your feature:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes

3. Run tests:
   ```bash
   pytest
   ```

4. Format code:
   ```bash
   black src/ tests/
   ```

5. Check code quality:
   ```bash
   flake8 src/ tests/
   mypy src/
   ```

6. Commit your changes:
   ```bash
   git add .
   git commit -m "Add feature: description"
   ```

7. Push to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

8. Create a Pull Request

## Code Style

- Follow PEP 8 guidelines
- Use type hints where appropriate
- Write docstrings for public functions and classes
- Keep functions focused and concise
- Use meaningful variable names

Example:

```python
def send_request(url: str, method: str = "GET") -> Response:
    """
    Send HTTP request to the specified URL.

    Args:
        url: Target URL
        method: HTTP method (default: GET)

    Returns:
        Response object

    Raises:
        RequestError: If request fails
    """
    # Implementation
```

## Testing

- Write tests for new features
- Ensure all tests pass before submitting PR
- Aim for high test coverage
- Use descriptive test names

Example:

```python
def test_send_get_request_with_headers():
    """Test sending GET request with custom headers"""
    # Test implementation
```

## Documentation

- Update README.md if adding new features
- Add docstrings to new functions/classes
- Update relevant documentation in `docs/`
- Include examples for new features

## Plugin Development

If contributing a plugin:

1. Place plugin in `examples/plugins/`
2. Include `manifest.json` and `plugin.py`
3. Add documentation in plugin README
4. Test plugin with various requests

## Bug Reports

When reporting bugs, include:

- Equinox version
- Python version
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Error messages/stack traces
- Screenshots (for GUI issues)

## Feature Requests

When requesting features:

- Describe the use case
- Explain why it's useful
- Suggest implementation approach
- Consider plugin alternative

## Pull Request Guidelines

- One feature/fix per PR
- Reference related issues
- Update CHANGELOG.md
- Include tests
- Update documentation
- Keep commits focused
- Write clear commit messages

## Code Review Process

1. Maintainers will review your PR
2. Address any feedback
3. Once approved, PR will be merged
4. Your contribution will be credited

## Questions?

- Open an issue for questions
- Join discussions
- Check existing issues/PRs

Thank you for contributing to Equinox!
