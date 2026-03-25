"""CLI entry point — ``code-intel-mcp serve`` and ``code-intel-mcp setup``."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import click

# ---------------------------------------------------------------------------
# Shared constants (mirror server.py defaults)
# ---------------------------------------------------------------------------
_BASE_DIR = Path.home() / ".code-intel-mcp"
_REPO_DIR = _BASE_DIR / "repos"
_INDEX_DIR = _BASE_DIR / "index"
_BIN_DIR = _BASE_DIR / "bin"


@click.group()
def main() -> None:
    """code-intel-mcp — MCP server for git lifecycle, code search, and dependency analysis."""


@main.command()
def serve() -> None:
    """Start the MCP server with stdio transport."""
    from code_intel_mcp.server import mcp as mcp_server

    mcp_server.run(transport="stdio")


@main.command()
@click.option(
    "--zoekt-url",
    envvar="ZOEKT_BINARY_URL",
    default=None,
    help="Base URL where Zoekt binary archives are hosted.",
)
def setup(zoekt_url: str | None) -> None:
    """Create directories, verify dependencies, and auto-install Zoekt if needed."""
    from code_intel_mcp.zoekt_installer import find_binary, install

    click.echo("code-intel-mcp setup")
    click.echo("=" * 40)

    # --- 1. Create directory structure ---
    click.echo()
    click.echo("Creating directory structure …")
    for d in (_BASE_DIR, _REPO_DIR, _INDEX_DIR, _BIN_DIR):
        d.mkdir(parents=True, exist_ok=True)
        click.echo(f"  ✓ {d}")

    # --- 2. Check git ---
    click.echo()
    click.echo("Checking dependencies …")
    git_path = shutil.which("git")
    if git_path:
        click.echo(f"  ✓ git — {git_path}")
    else:
        click.echo("  ✗ git — NOT FOUND")
        click.echo("    Install: https://git-scm.com/downloads")

    # --- 3. Check / auto-install Zoekt ---
    zoekt_index_path = find_binary("zoekt-index")
    zoekt_webserver_path = find_binary("zoekt-webserver")

    if zoekt_index_path and zoekt_webserver_path:
        click.echo(f"  ✓ zoekt-index — {zoekt_index_path}")
        click.echo(f"  ✓ zoekt-webserver — {zoekt_webserver_path}")
    else:
        click.echo("  ✗ Zoekt binaries not found")

        if zoekt_url:
            click.echo()
            click.echo("Auto-installing Zoekt binaries …")
            try:
                bin_dir = install(base_url=zoekt_url)
                click.echo(f"  ✓ Zoekt installed to {bin_dir}")
                zoekt_index_path = find_binary("zoekt-index")
                zoekt_webserver_path = find_binary("zoekt-webserver")
            except RuntimeError as exc:
                click.echo(f"  ✗ Auto-install failed: {exc}")
        else:
            click.echo()
            click.echo("To auto-install Zoekt, re-run with a download URL:")
            click.echo('  code-intel-mcp setup --zoekt-url "https://your-host/path/to/binaries"')
            click.echo()
            click.echo("Or install manually:")
            click.echo("  go install github.com/sourcegraph/zoekt/cmd/zoekt-index@latest")
            click.echo("  go install github.com/sourcegraph/zoekt/cmd/zoekt-webserver@latest")

    # --- 4. Summary ---
    all_found = git_path and zoekt_index_path and zoekt_webserver_path

    click.echo()
    if all_found:
        click.echo("All dependencies satisfied!")
        click.echo()
        click.echo("Configuration summary:")
        click.echo(f"  Repo store : {_REPO_DIR}")
        click.echo(f"  Index dir  : {_INDEX_DIR}")
        click.echo(f"  Binaries   : {_BIN_DIR}")
        click.echo(f"  Config file: {_BASE_DIR / 'config.json'}")
        click.echo()
        click.echo("Next steps:")
        click.echo("  1. Add the server to your MCP client configuration")
        click.echo('  2. Run "code-intel-mcp serve" to start the server')
        click.echo('  3. Use "repo_add" to register your first repository')
    else:
        click.echo("Some dependencies are missing — see above for install instructions.")
        sys.exit(1)
