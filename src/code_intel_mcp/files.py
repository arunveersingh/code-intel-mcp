"""File browsing operations on managed repositories."""

from __future__ import annotations

from pathlib import Path

from code_intel_mcp.errors import CodeIntelFileNotFoundError, RepoNotFoundError
from code_intel_mcp.models import DirEntry, RepoOverview
from code_intel_mcp.registry import Registry

# README filenames to check, in priority order.
_README_CANDIDATES = ("README.md", "README.rst", "README")

# Build config files to check for the overview summary.
_BUILD_CONFIGS = ("pom.xml", "build.gradle", "build.gradle.kts", "package.json")


class FileBrowser:
    """Direct filesystem operations on managed repositories."""

    def __init__(self, repo_store: Path, registry: Registry) -> None:
        self._repo_store = repo_store
        self._registry = registry

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_repo_root(self, repo_name: str) -> Path:
        """Return the on-disk root for *repo_name*, or raise."""
        repo = self._registry.get(repo_name)
        if repo is None:
            raise RepoNotFoundError(
                f"Repository '{repo_name}' is not managed.",
                details={"repo_name": repo_name},
            )
        return Path(str(repo.local_path)).resolve()

    def _safe_resolve(self, repo_root: Path, relative: str) -> Path:
        """Resolve *relative* against *repo_root* with traversal prevention.

        Raises ``CodeIntelFileNotFoundError`` when the resolved path escapes
        the repository directory.
        """
        resolved = (repo_root / relative).resolve()
        # Ensure the resolved path is within the repo root.
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            raise CodeIntelFileNotFoundError(
                f"Path '{relative}' is outside the repository.",
                details={"path": relative},
            ) from None
        return resolved

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_file(self, repo_name: str, file_path: str) -> str:
        """Read and return the contents of a file inside a managed repo.

        Raises ``CodeIntelFileNotFoundError`` if the file does not exist or
        the path attempts directory traversal.
        """
        repo_root = self._resolve_repo_root(repo_name)
        resolved = self._safe_resolve(repo_root, file_path)

        if not resolved.is_file():
            raise CodeIntelFileNotFoundError(
                f"File not found: {file_path}",
                details={"repo_name": repo_name, "file_path": file_path},
            )

        return resolved.read_text(encoding="utf-8")

    def list_directory(self, repo_name: str, dir_path: str = "") -> list[DirEntry]:
        """Return a directory listing for a path inside a managed repo.

        Raises ``CodeIntelFileNotFoundError`` if the directory does not exist
        or the path attempts directory traversal.
        """
        repo_root = self._resolve_repo_root(repo_name)
        resolved = self._safe_resolve(repo_root, dir_path) if dir_path else repo_root

        if not resolved.is_dir():
            raise CodeIntelFileNotFoundError(
                f"Directory not found: {dir_path}",
                details={"repo_name": repo_name, "dir_path": dir_path},
            )

        entries: list[DirEntry] = []
        for child in sorted(resolved.iterdir()):
            is_dir = child.is_dir()
            size = child.stat().st_size if not is_dir else None
            entries.append(DirEntry(name=child.name, is_directory=is_dir, size=size))
        return entries

    def get_repo_overview(self, repo_name: str) -> RepoOverview:
        """Return a summary overview of a managed repository."""
        repo_root = self._resolve_repo_root(repo_name)

        # --- README ---
        readme_content: str | None = None
        for candidate in _README_CANDIDATES:
            readme_path = repo_root / candidate
            if readme_path.is_file():
                readme_content = readme_path.read_text(encoding="utf-8")
                break

        # --- Top-level directory listing ---
        directory_listing = self.list_directory(repo_name)

        # --- Build config summary ---
        found_configs: list[str] = []
        for cfg in _BUILD_CONFIGS:
            if (repo_root / cfg).is_file():
                found_configs.append(cfg)

        build_summary: str | None = None
        if found_configs:
            build_summary = "Build configurations found: " + ", ".join(found_configs)

        return RepoOverview(
            repo_name=repo_name,
            readme_content=readme_content,
            directory_listing=directory_listing,
            build_summary=build_summary,
        )
