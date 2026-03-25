"""Data models for code-intel-mcp."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class IndexStatus(Enum):
    """State of a managed repository's Zoekt search index."""

    CURRENT = "current"
    STALE = "stale"
    MISSING = "missing"


@dataclass
class ManagedRepo:
    """A git repository managed by code-intel-mcp."""

    name: str
    git_url: str
    local_path: Path
    current_ref: str
    last_pull: datetime | None = None
    index_status: IndexStatus = IndexStatus.MISSING


@dataclass
class CommitInfo:
    """Details of a single git commit."""

    sha: str
    author: str
    message: str
    timestamp: datetime


@dataclass
class PullResult:
    """Result of pulling latest changes for a repository."""

    repo_name: str
    updated: bool
    new_commits: int


@dataclass
class CheckoutResult:
    """Result of checking out a ref in a repository."""

    repo_name: str
    previous_ref: str
    new_ref: str


@dataclass
class SyncResult:
    """Result of syncing a single repository during sync-all."""

    repo_name: str
    success: bool
    error: str | None = None
    updated: bool = False


@dataclass
class SearchResult:
    """A single code search match."""

    repo_name: str
    file_path: str
    line_number: int
    content: str
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


@dataclass
class FileMatch:
    """A file name search match."""

    repo_name: str
    file_path: str


@dataclass
class Dependency:
    """A declared dependency from a build configuration file."""

    group_id: str
    artifact_id: str
    version: str | None = None
    scope: str | None = None


@dataclass
class InternalDependency:
    """A dependency that maps to another managed repository."""

    dependency: Dependency
    matched_repo: str


@dataclass
class DependencyReport:
    """Dependency analysis result for a repository."""

    repo_name: str
    build_file: str | None
    dependencies: list[Dependency] = field(default_factory=list)
    internal_dependencies: list[InternalDependency] = field(default_factory=list)
    message: str | None = None


@dataclass
class DirEntry:
    """A single entry in a directory listing."""

    name: str
    is_directory: bool
    size: int | None = None


@dataclass
class RepoOverview:
    """Summary overview of a repository."""

    repo_name: str
    readme_content: str | None
    directory_listing: list[DirEntry]
    build_summary: str | None


@dataclass
class GitLabProject:
    """A project retrieved from the GitLab REST API."""

    name: str
    path_with_namespace: str
    http_url_to_repo: str
    ssh_url_to_repo: str


@dataclass
class BinaryStatus:
    """Availability status of required external binaries."""

    zoekt_index_found: bool
    zoekt_webserver_found: bool
    git_found: bool
