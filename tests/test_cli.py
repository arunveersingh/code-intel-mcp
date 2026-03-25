"""Tests for the CLI entry point (setup and serve commands)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

click = pytest.importorskip("click")
from click.testing import CliRunner

_FB = "code_intel_mcp.zoekt_installer.find_binary"
_IS = "code_intel_mcp.zoekt_installer.is_installed"
_SW = "shutil.which"


def _import_cli():
    from code_intel_mcp import cli
    return cli


class TestSetupCommand:

    def test_setup_creates_directories(self, tmp_path: Path):
        cli = _import_cli()
        base = tmp_path / ".code-intel-mcp"

        with patch.object(cli, "_BASE_DIR", base), \
             patch.object(cli, "_REPO_DIR", base / "repos"), \
             patch.object(cli, "_INDEX_DIR", base / "index"), \
             patch.object(cli, "_BIN_DIR", base / "bin"), \
             patch(_SW, return_value="/usr/bin/git"), \
             patch(_FB, return_value="/usr/local/bin/fake"):
            result = CliRunner().invoke(cli.main, ["setup"])

        assert result.exit_code == 0
        assert (base / "repos").is_dir()
        assert (base / "index").is_dir()
        assert (base / "bin").is_dir()

    def test_setup_reports_missing_zoekt(self, tmp_path: Path):
        cli = _import_cli()
        base = tmp_path / ".code-intel-mcp"

        with patch.object(cli, "_BASE_DIR", base), \
             patch.object(cli, "_REPO_DIR", base / "repos"), \
             patch.object(cli, "_INDEX_DIR", base / "index"), \
             patch.object(cli, "_BIN_DIR", base / "bin"), \
             patch(_SW, return_value="/usr/bin/git"), \
             patch(_FB, return_value=None), \
             patch(_IS, return_value=False):
            result = CliRunner().invoke(cli.main, ["setup"])

        assert result.exit_code == 1
        assert "Zoekt binaries not found" in result.output
        assert "missing" in result.output.lower()

    def test_setup_reports_missing_git(self, tmp_path: Path):
        cli = _import_cli()
        base = tmp_path / ".code-intel-mcp"

        with patch.object(cli, "_BASE_DIR", base), \
             patch.object(cli, "_REPO_DIR", base / "repos"), \
             patch.object(cli, "_INDEX_DIR", base / "index"), \
             patch.object(cli, "_BIN_DIR", base / "bin"), \
             patch(_SW, return_value=None), \
             patch(_FB, return_value=None), \
             patch(_IS, return_value=False):
            result = CliRunner().invoke(cli.main, ["setup"])

        assert result.exit_code == 1
        assert "git" in result.output
        assert "NOT FOUND" in result.output

    def test_setup_prints_success_summary(self, tmp_path: Path):
        cli = _import_cli()
        base = tmp_path / ".code-intel-mcp"

        with patch.object(cli, "_BASE_DIR", base), \
             patch.object(cli, "_REPO_DIR", base / "repos"), \
             patch.object(cli, "_INDEX_DIR", base / "index"), \
             patch.object(cli, "_BIN_DIR", base / "bin"), \
             patch(_SW, return_value="/usr/bin/git"), \
             patch(_FB, return_value="/usr/local/bin/fake"):
            result = CliRunner().invoke(cli.main, ["setup"])

        assert result.exit_code == 0
        assert "All dependencies satisfied" in result.output
        assert "Repo store" in result.output
        assert "Index dir" in result.output
        assert "Next steps" in result.output
        assert "repo_add" in result.output
