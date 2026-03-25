"""MCP server — tool registration, request routing, and lifecycle management."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from code_intel_mcp.dependencies import DependencyParser
from code_intel_mcp.errors import CodeIntelError, RepoNotFoundError
from code_intel_mcp.files import FileBrowser
from code_intel_mcp.git_manager import GitManager
from code_intel_mcp.models import IndexStatus
from code_intel_mcp.registry import Registry
from code_intel_mcp.search import SearchService
from code_intel_mcp.zoekt import ZoektLifecycle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------
_BASE_DIR = Path.home() / ".code-intel-mcp"
_REPO_STORE = _BASE_DIR / "repos"
_INDEX_DIR = _BASE_DIR / "index"
_CONFIG_PATH = _BASE_DIR / "config.json"

# ---------------------------------------------------------------------------
# Module-level service instances (initialised in lifespan)
# ---------------------------------------------------------------------------
registry: Registry | None = None
zoekt: ZoektLifecycle | None = None
git_manager: GitManager | None = None
search_service: SearchService | None = None
file_browser: FileBrowser | None = None
dep_parser: DependencyParser | None = None


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(server: FastMCP):
    """Initialise and tear down all service layers."""
    global registry, zoekt, git_manager, search_service, file_browser, dep_parser

    # Ensure ~/.code-intel-mcp/bin is on PATH so Zoekt binaries are found
    bin_dir = str(_BASE_DIR / "bin")
    current_path = os.environ.get("PATH", "")
    if bin_dir not in current_path:
        os.environ["PATH"] = bin_dir + os.pathsep + current_path

    _REPO_STORE.mkdir(parents=True, exist_ok=True)
    _INDEX_DIR.mkdir(parents=True, exist_ok=True)

    registry = Registry(config_path=_CONFIG_PATH)
    registry.load()

    zoekt = ZoektLifecycle(index_dir=_INDEX_DIR)
    await zoekt.verify_binaries()

    if registry.list_all():
        try:
            await zoekt.start_webserver()
        except CodeIntelError:
            logger.warning("Failed to start Zoekt webserver during startup — search will be unavailable")

    git_manager = GitManager(repo_store=_REPO_STORE, registry=registry, zoekt=zoekt)
    search_service = SearchService()
    file_browser = FileBrowser(repo_store=_REPO_STORE, registry=registry)
    dep_parser = DependencyParser()

    missing = registry.validate_paths()
    if missing:
        for name in missing:
            logger.warning("Registered repo '%s' has missing local path", name)
            registry.update(name, index_status=IndexStatus.MISSING)
        registry.save()

    logger.info("code-intel-mcp server started (%d repos loaded)", len(registry.list_all()))

    yield

    if zoekt is not None:
        await zoekt.stop_webserver()
    logger.info("code-intel-mcp server stopped")


mcp = FastMCP("code-intel-mcp", lifespan=lifespan)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _serialize_result(obj: object) -> str:
    if hasattr(obj, "__dataclass_fields__"):
        raw = asdict(obj)
    elif isinstance(obj, list):
        raw = [asdict(item) if hasattr(item, "__dataclass_fields__") else item for item in obj]
    elif isinstance(obj, dict):
        raw = obj
    else:
        raw = obj
    return json.dumps(raw, default=_json_default, indent=2)


def _json_default(o: object) -> object:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, IndexStatus):
        return o.value
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _error_result(exc: CodeIntelError) -> str:
    payload = {
        "error_type": type(exc).__name__,
        "message": exc.message,
        "details": exc.details,
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# Tool handlers — Repository management
# ---------------------------------------------------------------------------

@mcp.tool()
async def repo_add(url: str, ref: str | None = None) -> str:
    """Clone a git repository and register it for code search.

    Args:
        url: The git clone URL.
        ref: Optional branch, tag, or commit SHA to checkout after cloning.
    """
    try:
        managed = await git_manager.clone(url, ref=ref)
        if zoekt is not None and not zoekt.is_webserver_running():
            try:
                await zoekt.start_webserver()
            except CodeIntelError:
                logger.warning("Could not start Zoekt webserver after first clone")
        return _serialize_result(managed)
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def repo_add_gitlab_group(group_path: str) -> str:
    """Clone all repositories from a GitLab group.

    Args:
        group_path: The GitLab group path (e.g. "my-org/my-team").
    """
    try:
        result = await git_manager.clone_gitlab_group(group_path)
        if zoekt is not None and not zoekt.is_webserver_running() and result.cloned:
            try:
                await zoekt.start_webserver()
            except CodeIntelError:
                logger.warning("Could not start Zoekt webserver after group clone")
        return _serialize_result(result)
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def repo_list() -> str:
    """List all managed repositories with their status."""
    try:
        repos = registry.list_all()
        summaries = []
        for r in repos:
            summaries.append({
                "name": r.name,
                "git_url": r.git_url,
                "current_ref": r.current_ref,
                "last_pull": r.last_pull.isoformat() if r.last_pull else None,
                "index_status": r.index_status.value,
            })
        return _serialize_result(summaries)
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def repo_info(repo_name: str) -> str:
    """Get detailed information about a managed repository.

    Args:
        repo_name: Name of the managed repository.
    """
    try:
        managed = registry.get(repo_name)
        if managed is None:
            raise RepoNotFoundError(
                f"Repository '{repo_name}' is not managed.",
                details={"repo_name": repo_name},
            )
        branches = git_manager.get_branches(repo_name)
        tags = git_manager.get_tags(repo_name)
        last_commit = git_manager.get_last_commit(repo_name)
        disk_size = git_manager.get_disk_size(repo_name)
        info = {
            "name": managed.name,
            "git_url": managed.git_url,
            "local_path": str(managed.local_path),
            "current_ref": managed.current_ref,
            "last_pull": managed.last_pull.isoformat() if managed.last_pull else None,
            "index_status": managed.index_status.value,
            "branches": branches,
            "tags": tags,
            "last_commit": asdict(last_commit),
            "disk_size": disk_size,
        }
        return _serialize_result(info)
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def repo_checkout(repo_name: str, ref: str) -> str:
    """Checkout a branch, tag, or commit SHA in a managed repository.

    Args:
        repo_name: Name of the managed repository.
        ref: Branch name, tag name, or commit SHA to checkout.
    """
    try:
        result = await git_manager.checkout(repo_name, ref)
        return _serialize_result(result)
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def repo_pull(repo_name: str) -> str:
    """Pull latest changes from remote for a managed repository.

    Args:
        repo_name: Name of the managed repository.
    """
    try:
        result = await git_manager.pull(repo_name)
        return _serialize_result(result)
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def repo_sync_all() -> str:
    """Pull latest changes for every managed repository."""
    try:
        results = await git_manager.sync_all()
        return _serialize_result(results)
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def repo_remove(repo_name: str) -> str:
    """Remove a managed repository, its local files, and its search index.

    Args:
        repo_name: Name of the managed repository to remove.
    """
    try:
        await git_manager.remove(repo_name)
        return _serialize_result({"removed": repo_name})
    except CodeIntelError as exc:
        return _error_result(exc)


# ---------------------------------------------------------------------------
# Tool handlers — Search
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_code(
    query: str,
    repos: list[str] | None = None,
    language: str | None = None,
    file_pattern: str | None = None,
) -> str:
    """Search for code patterns across indexed repositories.

    Args:
        query: The search query string.
        repos: Optional list of repo names to restrict search to.
        language: Optional language filter (e.g. "java", "python").
        file_pattern: Optional file path pattern filter.
    """
    try:
        results = await search_service.search_code(
            query, repos=repos, language=language, file_pattern=file_pattern,
        )
        return _serialize_result(results)
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def search_files(
    pattern: str,
    repos: list[str] | None = None,
) -> str:
    """Search for files by name pattern across indexed repositories.

    Args:
        pattern: Filename pattern to search for.
        repos: Optional list of repo names to restrict search to.
    """
    try:
        results = await search_service.search_files(pattern, repos=repos)
        return _serialize_result(results)
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def search_references(
    symbol: str,
    repos: list[str] | None = None,
) -> str:
    """Search for symbol references across indexed repositories.

    Args:
        symbol: The symbol name to search for (uses word-boundary matching).
        repos: Optional list of repo names to restrict search to.
    """
    try:
        results = await search_service.search_references(symbol, repos=repos)
        return _serialize_result(results)
    except CodeIntelError as exc:
        return _error_result(exc)


# ---------------------------------------------------------------------------
# Tool handlers — File browsing
# ---------------------------------------------------------------------------

@mcp.tool()
async def read_file(repo_name: str, file_path: str) -> str:
    """Read the contents of a file in a managed repository.

    Args:
        repo_name: Name of the managed repository.
        file_path: Relative path to the file within the repository.
    """
    try:
        content = file_browser.read_file(repo_name, file_path)
        return _serialize_result({"repo_name": repo_name, "file_path": file_path, "content": content})
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def list_directory(repo_name: str, path: str = "") -> str:
    """List directory contents in a managed repository.

    Args:
        repo_name: Name of the managed repository.
        path: Relative directory path (empty string for repo root).
    """
    try:
        entries = file_browser.list_directory(repo_name, path)
        return _serialize_result(entries)
    except CodeIntelError as exc:
        return _error_result(exc)


@mcp.tool()
async def get_repo_overview(repo_name: str) -> str:
    """Get a summary overview of a managed repository.

    Args:
        repo_name: Name of the managed repository.
    """
    try:
        overview = file_browser.get_repo_overview(repo_name)
        return _serialize_result(overview)
    except CodeIntelError as exc:
        return _error_result(exc)


# ---------------------------------------------------------------------------
# Tool handlers — Dependency analysis
# ---------------------------------------------------------------------------

@mcp.tool()
async def find_dependencies(repo_name: str) -> str:
    """Analyze dependencies for a managed repository.

    Parses build configuration files (pom.xml, build.gradle, package.json)
    and flags internal cross-repo dependencies.

    Args:
        repo_name: Name of the managed repository.
    """
    try:
        managed = registry.get(repo_name)
        if managed is None:
            raise RepoNotFoundError(
                f"Repository '{repo_name}' is not managed.",
                details={"repo_name": repo_name},
            )
        report = dep_parser.parse(managed.local_path)
        all_repos = registry.list_all()
        internal = dep_parser.find_internal_deps(report.dependencies, all_repos)
        report.internal_dependencies = internal
        return _serialize_result(report)
    except CodeIntelError as exc:
        return _error_result(exc)
