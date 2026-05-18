## Description

<!-- Brief summary of the change -->

## Type of Change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that causes existing functionality to change)
- [ ] Documentation update
- [ ] Security hardening
- [ ] Performance optimization
- [ ] Refactoring

## Related Issues

Closes #(issue number, if applicable)

## Motivation and Context

<!-- Explain why this change is needed -->

## Testing

<!-- Describe how you tested your changes -->
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing completed

## Checklist

### Code Quality

- [ ] Code follows project style guide (run `pre-commit run --all-files` locally)
- [ ] Comments added for complex logic
- [ ] Docstrings added/updated for public APIs
- [ ] No unnecessary complexity introduced

### Security & Validation

- [ ] All user inputs validated via `Validator` facade
- [ ] No hardcoded secrets or credentials
- [ ] No SQL string formatting (parameterized queries only)
- [ ] Security scan passes: `bandit -r src/equinox -ll` (medium/high/critical)
- [ ] No new high-severity security findings

### Type Safety

- [ ] Type hints added to new functions/methods
- [ ] Mypy passes: `mypy src/equinox tests`
- [ ] No `# type: ignore` comments added without justification

### Testing

- [ ] Unit test coverage maintained (>85% overall)
- [ ] No flaky tests added
- [ ] Tests pass on Python 3.9, 3.10, 3.11

### Documentation

- [ ] README.md updated if user-facing
- [ ] AGENTS.md updated if architecture changed
- [ ] Changelog entry added (if not a minor fix)
- [ ] Docstrings updated if API changed

### Performance

- [ ] No unintended performance regression
- [ ] Large operations logged with timing info
- [ ] Benchmark baseline confirmed (if applicable)

## Reviewer Notes

<!-- Optional: add context that helps reviewers understand the change -->

## Screenshots (if applicable)

<!-- For GUI changes, add before/after screenshots -->

