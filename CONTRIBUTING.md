# Contributing to code-intel-mcp

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

For verbose output:

```bash
pytest -v
```

## Coding Standards

- Python 3.11+ with type hints on all public APIs
- Use `dataclasses` for data models
- Async functions for I/O-bound operations
- Custom exceptions from `errors.py` hierarchy — no bare `Exception` raises
- No hardcoded credentials or internal URLs

## Project Structure

```
src/code_intel_mcp/
├── cli.py           # CLI entry point (click)
├── dependencies.py  # Build config parsing
├── errors.py        # Exception hierarchy
├── files.py         # File browser
├── git_manager.py   # Git operations
├── gitlab_client.py # GitLab REST API client
├── models.py        # Data models
├── registry.py      # JSON persistence
├── search.py        # Zoekt search service
├── server.py        # MCP server and tool handlers
└── zoekt.py         # Zoekt process lifecycle
```

## Pull Request Guidelines

1. Create a feature branch from `main`
2. Write tests for new functionality
3. Ensure all tests pass (`pytest`)
4. Keep commits focused and well-described
5. Open a PR with a clear description of the change
