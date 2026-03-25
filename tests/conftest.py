"""Shared test fixtures for code-intel-mcp tests."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_repo_store(tmp_path: Path) -> Path:
    """Provide a temporary directory to act as the repo store."""
    repo_store = tmp_path / "repos"
    repo_store.mkdir()
    return repo_store


@pytest.fixture
def tmp_index_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory to act as the Zoekt index directory."""
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    return index_dir


@pytest.fixture
def tmp_config_path(tmp_path: Path) -> Path:
    """Provide a temporary path for the registry config JSON file."""
    return tmp_path / "config.json"


@pytest.fixture
def mock_registry_path(tmp_path: Path) -> Path:
    """Provide a pre-populated registry config file with an empty registry."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"repos": [], "version": 1}, indent=2))
    return config_path
