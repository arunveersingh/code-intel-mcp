"""Git lifecycle manager — clone, pull, checkout, remove via gitpython."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import git
import git.exc

from code_intel_mcp.errors import (
    GitLabError,
    GitOperationError,
    RepoAlreadyExistsError,
    RepoNotFoundError,
)
from code_intel_mcp.models import (
    CheckoutResult,
    CommitInfo,
    GitLabProject,
    IndexStatus,
    ManagedRepo,
    PullResult,
    SyncResult,
)
from code_intel_mcp.registry import Registry
from code_intel_mcp.zoekt import ZoektLifecycle

logger = logging.getLogger(__name__)


@dataclass
class GroupCloneResult:
    """Summary of a GitLab group clone operation."""

    group_path: str
    cloned: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)


def _derive_repo_name(url: str, existing_names: set[str]) -> str:
    """Derive a short repo name from a git URL.

    Strategy:
      1. Parse the URL path, strip trailing .git and slashes.
      2. Use the last path component as the name (e.g. ``my-repo``).
      3. On collision with *existing_names*, prefix with the parent
         component: ``group/my-repo``.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]

    parts = [p for p in path.split("/") if p]
    if not parts:
        raise GitOperationError(
            f"Cannot derive repo name from URL: {url}",
            details={"url": url},
        )

    short_name = parts[-1]
    if short_name not in existing_names:
        return short_name

    # Collision — use group/name
    if len(parts) >= 2:
        qualified = f"{parts[-2]}/{parts[-1]}"
        return qualified

    return short_name


class GitManager:
    """Manages git operations for all tracked repositories."""

    def __init__(
        self,
        repo_store: Path,
        registry: Registry,
        zoekt: ZoektLifecycle,
    ) -> None:
        self.repo_store = repo_store
        self.registry = registry
        self.zoekt = zoekt

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_repo_or_raise(self, repo_name: str) -> ManagedRepo:
        """Look up a managed repo by name, raising if not found."""
        repo = self.registry.get(repo_name)
        if repo is None:
            raise RepoNotFoundError(
                f"Repository '{repo_name}' is not managed.",
                details={"repo_name": repo_name},
            )
        return repo

    def _open_git_repo(self, managed: ManagedRepo) -> git.Repo:
        """Open a gitpython Repo object, wrapping errors."""
        try:
            return git.Repo(str(managed.local_path))
        except git.exc.InvalidGitRepositoryError as exc:
            raise GitOperationError(
                f"Invalid git repository for '{managed.name}': {exc}",
                details={"repo_name": managed.name},
            ) from exc

    def _check_duplicate_url(self, url: str) -> None:
        """Raise if *url* is already registered."""
        for repo in self.registry.list_all():
            if repo.git_url == url:
                raise RepoAlreadyExistsError(
                    f"Repository with URL '{url}' is already managed as '{repo.name}'.",
                    details={"url": url, "existing_name": repo.name},
                )

    # ------------------------------------------------------------------
    # Core async operations
    # ------------------------------------------------------------------

    async def clone(self, url: str, ref: str | None = None) -> ManagedRepo:
        """Clone a repository into the repo store.

        Derives the repo name from the URL. On name collision, uses
        ``group/name`` form. Registers in the Registry and triggers
        Zoekt indexing.
        """
        self._check_duplicate_url(url)

        existing_names = {r.name for r in self.registry.list_all()}
        repo_name = _derive_repo_name(url, existing_names)

        # Build local path — use sanitised name for filesystem
        safe_dir = repo_name.replace("/", os.sep)
        local_path = self.repo_store / safe_dir

        try:
            logger.info("Cloning %s into %s", url, local_path)
            git_repo = git.Repo.clone_from(url, str(local_path))
        except git.exc.GitCommandError as exc:
            raise GitOperationError(
                f"Failed to clone '{url}': {exc}",
                details={"url": url, "repo_name": repo_name},
            ) from exc

        # Checkout specific ref if requested
        current_ref = git_repo.active_branch.name if not git_repo.head.is_detached else git_repo.head.commit.hexsha
        if ref is not None:
            try:
                git_repo.git.checkout(ref)
                # Update current_ref after checkout
                if git_repo.head.is_detached:
                    current_ref = git_repo.head.commit.hexsha
                else:
                    current_ref = git_repo.active_branch.name
            except git.exc.GitCommandError as exc:
                raise GitOperationError(
                    f"Failed to checkout ref '{ref}' in '{repo_name}': {exc}",
                    details={"url": url, "repo_name": repo_name, "ref": ref},
                ) from exc

        managed = ManagedRepo(
            name=repo_name,
            git_url=url,
            local_path=local_path,
            current_ref=current_ref,
        )

        self.registry.add(managed)
        self.registry.save()

        # Trigger indexing
        try:
            await self.zoekt.index_repo(local_path)
            self.registry.update(repo_name, index_status=IndexStatus.CURRENT)
            self.registry.save()
        except Exception:
            logger.warning("Indexing failed for %s, marking as MISSING", repo_name)
            self.registry.update(repo_name, index_status=IndexStatus.MISSING)
            self.registry.save()

        return managed

    async def clone_gitlab_group(
        self,
        group_path: str,
        client: object | None = None,
    ) -> GroupCloneResult:
        """Clone all repositories from a GitLab group.

        Parameters
        ----------
        group_path:
            The GitLab group path (e.g. ``"my-org/my-team"``).
        client:
            An optional ``GitLabClient`` instance.  When *None* a client
            is created via ``GitLabClient.from_env()``.

        Returns a ``GroupCloneResult`` summarising what was cloned,
        skipped, and what failed.
        """
        from code_intel_mcp.gitlab_client import GitLabClient  # avoid circular import

        if client is None:
            client = GitLabClient.from_env()

        # Fetch project list from GitLab
        try:
            projects: list[GitLabProject] = await client.list_group_projects(group_path)
        except GitLabError:
            raise  # already descriptive with group_path
        except Exception as exc:
            raise GitLabError(
                f"Failed to list projects for GitLab group '{group_path}': {exc}",
                details={"group_path": group_path},
            ) from exc

        # Build set of already-registered URLs for fast lookup
        registered_urls = {r.git_url for r in self.registry.list_all()}

        result = GroupCloneResult(group_path=group_path)

        for project in projects:
            clone_url = project.http_url_to_repo

            # Skip repos already managed
            if clone_url in registered_urls:
                result.skipped.append(project.path_with_namespace)
                logger.info(
                    "Skipping already-managed project %s",
                    project.path_with_namespace,
                )
                continue

            # Clone the project
            try:
                await self.clone(clone_url)
                result.cloned.append(project.path_with_namespace)
            except RepoAlreadyExistsError:
                # Race-condition guard: another project with same URL
                result.skipped.append(project.path_with_namespace)
            except Exception as exc:
                logger.warning(
                    "Failed to clone project %s: %s",
                    project.path_with_namespace,
                    exc,
                )
                result.failed.append(
                    {
                        "project": project.path_with_namespace,
                        "error": str(exc),
                    }
                )

        return result

    async def pull(self, repo_name: str) -> PullResult:
        """Pull latest changes from remote for a managed repo."""
        managed = self._get_repo_or_raise(repo_name)
        git_repo = self._open_git_repo(managed)

        try:
            # Count commits before pull
            before_sha = git_repo.head.commit.hexsha
            git_repo.remotes.origin.pull()
            after_sha = git_repo.head.commit.hexsha

            updated = before_sha != after_sha
            new_commits = 0
            if updated:
                # Count commits between old and new HEAD
                new_commits = sum(
                    1 for _ in git_repo.iter_commits(f"{before_sha}..{after_sha}")
                )
        except git.exc.GitCommandError as exc:
            raise GitOperationError(
                f"Failed to pull '{repo_name}': {exc}",
                details={"repo_name": repo_name},
            ) from exc

        now = datetime.now(timezone.utc)
        self.registry.update(repo_name, last_pull=now)
        self.registry.save()

        # Re-index if changes were received
        if updated:
            try:
                await self.zoekt.index_repo(managed.local_path)
                self.registry.update(repo_name, index_status=IndexStatus.CURRENT)
                self.registry.save()
            except Exception:
                logger.warning("Re-indexing failed for %s after pull", repo_name)
                self.registry.update(repo_name, index_status=IndexStatus.STALE)
                self.registry.save()

        return PullResult(repo_name=repo_name, updated=updated, new_commits=new_commits)

    async def checkout(self, repo_name: str, ref: str) -> CheckoutResult:
        """Checkout a branch, tag, or commit SHA in a managed repo."""
        managed = self._get_repo_or_raise(repo_name)
        git_repo = self._open_git_repo(managed)

        previous_ref = managed.current_ref

        try:
            git_repo.git.checkout(ref)
        except git.exc.GitCommandError as exc:
            raise GitOperationError(
                f"Failed to checkout '{ref}' in '{repo_name}': {exc}",
                details={"repo_name": repo_name, "ref": ref},
            ) from exc

        # Determine new ref
        if git_repo.head.is_detached:
            new_ref = git_repo.head.commit.hexsha
        else:
            new_ref = git_repo.active_branch.name

        self.registry.update(repo_name, current_ref=new_ref)
        self.registry.save()

        # Trigger re-indexing
        try:
            await self.zoekt.index_repo(managed.local_path)
            self.registry.update(repo_name, index_status=IndexStatus.CURRENT)
            self.registry.save()
        except Exception:
            logger.warning("Re-indexing failed for %s after checkout", repo_name)
            self.registry.update(repo_name, index_status=IndexStatus.STALE)
            self.registry.save()

        return CheckoutResult(
            repo_name=repo_name,
            previous_ref=previous_ref,
            new_ref=new_ref,
        )

    async def sync_all(self) -> list[SyncResult]:
        """Pull every managed repo, returning per-repo results.

        Continues on individual failures so one broken repo doesn't
        block the rest.
        """
        results: list[SyncResult] = []
        for managed in self.registry.list_all():
            try:
                pull_result = await self.pull(managed.name)
                results.append(
                    SyncResult(
                        repo_name=managed.name,
                        success=True,
                        updated=pull_result.updated,
                    )
                )
            except Exception as exc:
                logger.warning("sync_all: failed to pull %s: %s", managed.name, exc)
                results.append(
                    SyncResult(
                        repo_name=managed.name,
                        success=False,
                        error=str(exc),
                    )
                )
        return results

    async def remove(self, repo_name: str) -> None:
        """Remove a managed repo: delete directory, registry entry, and index."""
        managed = self._get_repo_or_raise(repo_name)

        # Delete repo directory
        local_path = Path(str(managed.local_path))
        if local_path.exists():
            shutil.rmtree(local_path)
            logger.info("Deleted repo directory %s", local_path)

        # Remove from registry
        self.registry.remove(repo_name)
        self.registry.save()

        # Remove Zoekt index
        await self.zoekt.remove_index(repo_name)

    # ------------------------------------------------------------------
    # Synchronous helper methods
    # ------------------------------------------------------------------

    def get_branches(self, repo_name: str) -> list[str]:
        """Return list of branch names for a managed repo."""
        managed = self._get_repo_or_raise(repo_name)
        git_repo = self._open_git_repo(managed)
        try:
            return [ref.name for ref in git_repo.branches]
        except git.exc.GitCommandError as exc:
            raise GitOperationError(
                f"Failed to list branches for '{repo_name}': {exc}",
                details={"repo_name": repo_name},
            ) from exc

    def get_tags(self, repo_name: str) -> list[str]:
        """Return list of tag names for a managed repo."""
        managed = self._get_repo_or_raise(repo_name)
        git_repo = self._open_git_repo(managed)
        try:
            return [tag.name for tag in git_repo.tags]
        except git.exc.GitCommandError as exc:
            raise GitOperationError(
                f"Failed to list tags for '{repo_name}': {exc}",
                details={"repo_name": repo_name},
            ) from exc

    def get_last_commit(self, repo_name: str) -> CommitInfo:
        """Return details of the HEAD commit for a managed repo."""
        managed = self._get_repo_or_raise(repo_name)
        git_repo = self._open_git_repo(managed)
        try:
            commit = git_repo.head.commit
            return CommitInfo(
                sha=commit.hexsha,
                author=str(commit.author),
                message=commit.message.strip(),
                timestamp=commit.committed_datetime,
            )
        except (git.exc.GitCommandError, ValueError) as exc:
            raise GitOperationError(
                f"Failed to get last commit for '{repo_name}': {exc}",
                details={"repo_name": repo_name},
            ) from exc

    def get_disk_size(self, repo_name: str) -> int:
        """Return total disk size in bytes for a managed repo directory."""
        managed = self._get_repo_or_raise(repo_name)
        local_path = Path(str(managed.local_path))
        if not local_path.exists():
            return 0
        total = 0
        for dirpath, _dirnames, filenames in os.walk(local_path):
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    total += os.path.getsize(fpath)
                except OSError:
                    pass
        return total
