"""Tests for GitManager."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_intel_mcp.errors import (
    GitOperationError,
    RepoAlreadyExistsError,
    RepoNotFoundError,
)
from code_intel_mcp.git_manager import GitManager, _derive_repo_name
from code_intel_mcp.models import ManagedRepo
from code_intel_mcp.registry import Registry
from code_intel_mcp.zoekt import ZoektLifecycle

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def registry(tmp_config_path: Path) -> Registry:
    reg = Registry(config_path=tmp_config_path)
    reg.load()
    return reg


@pytest.fixture
def zoekt(tmp_index_dir: Path) -> ZoektLifecycle:
    z = ZoektLifecycle(index_dir=tmp_index_dir)
    z.index_repo = AsyncMock()
    z.remove_index = AsyncMock()
    return z


@pytest.fixture
def git_manager(tmp_repo_store: Path, registry: Registry, zoekt: ZoektLifecycle) -> GitManager:
    return GitManager(repo_store=tmp_repo_store, registry=registry, zoekt=zoekt)


# ------------------------------------------------------------------
# _derive_repo_name
# ------------------------------------------------------------------


class TestDeriveRepoName:
    def test_simple_https_url(self):
        name = _derive_repo_name("https://github.com/org/my-repo.git", set())
        assert name == "my-repo"

    def test_strips_trailing_slash(self):
        name = _derive_repo_name("https://github.com/org/my-repo.git/", set())
        assert name == "my-repo"

    def test_no_dot_git_suffix(self):
        name = _derive_repo_name("https://github.com/org/my-repo", set())
        assert name == "my-repo"

    def test_collision_uses_group_prefix(self):
        name = _derive_repo_name(
            "https://gitlab.example.com/team-b/utils.git", {"utils"}
        )
        assert name == "team-b/utils"

    def test_no_collision_returns_short(self):
        name = _derive_repo_name(
            "https://gitlab.example.com/team-b/utils.git", {"other"}
        )
        assert name == "utils"

    def test_empty_path_raises(self):
        with pytest.raises(GitOperationError):
            _derive_repo_name("https://github.com/", set())

    def test_ssh_url(self):
        name = _derive_repo_name("git@github.com:org/my-repo.git", set())
        assert name == "my-repo"


# ------------------------------------------------------------------
# GitManager.clone
# ------------------------------------------------------------------


class TestClone:
    @pytest.mark.asyncio
    async def test_clone_success(self, git_manager: GitManager, tmp_repo_store: Path):
        """Clone registers repo in registry and triggers indexing."""
        url = "https://github.com/org/test-repo.git"

        mock_git_repo = MagicMock()
        mock_git_repo.active_branch.name = "main"
        mock_git_repo.head.is_detached = False

        with patch("code_intel_mcp.git_manager.git.Repo.clone_from", return_value=mock_git_repo):
            result = await git_manager.clone(url)

        assert result.name == "test-repo"
        assert result.git_url == url
        assert result.current_ref == "main"

        # Registered in registry
        assert git_manager.registry.get("test-repo") is not None
        # Indexing triggered
        git_manager.zoekt.index_repo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clone_with_ref(self, git_manager: GitManager):
        """Clone with a specific ref checks out that ref."""
        url = "https://github.com/org/test-repo.git"

        mock_git_repo = MagicMock()
        mock_git_repo.active_branch.name = "main"
        mock_git_repo.head.is_detached = False

        with patch("code_intel_mcp.git_manager.git.Repo.clone_from", return_value=mock_git_repo):
            await git_manager.clone(url, ref="develop")

        mock_git_repo.git.checkout.assert_called_once_with("develop")

    @pytest.mark.asyncio
    async def test_clone_duplicate_url_raises(self, git_manager: GitManager):
        """Cloning a URL that's already registered raises RepoAlreadyExistsError."""
        url = "https://github.com/org/test-repo.git"
        existing = ManagedRepo(
            name="test-repo",
            git_url=url,
            local_path=Path("/tmp/test"),
            current_ref="main",
        )
        git_manager.registry.add(existing)

        with pytest.raises(RepoAlreadyExistsError):
            await git_manager.clone(url)

    @pytest.mark.asyncio
    async def test_clone_git_error_raises(self, git_manager: GitManager):
        """Git clone failure is wrapped in GitOperationError."""
        import git.exc

        url = "https://github.com/org/bad-repo.git"
        with patch(
            "code_intel_mcp.git_manager.git.Repo.clone_from",
            side_effect=git.exc.GitCommandError("clone", "fatal: repo not found"),
        ), pytest.raises(GitOperationError, match="bad-repo"):
            await git_manager.clone(url)


# ------------------------------------------------------------------
# GitManager.pull
# ------------------------------------------------------------------


class TestPull:
    @pytest.mark.asyncio
    async def test_pull_with_changes(self, git_manager: GitManager, tmp_repo_store: Path):
        """Pull that receives new commits triggers re-indexing."""
        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        mock_git_repo = MagicMock()
        mock_commit_before = MagicMock()
        mock_commit_before.hexsha = "aaa"
        mock_commit_after = MagicMock()
        mock_commit_after.hexsha = "bbb"

        # head.commit returns different shas before and after pull
        mock_git_repo.head.commit = mock_commit_before


        def pull_side_effect(*args, **kwargs):
            mock_git_repo.head.commit = mock_commit_after

        mock_git_repo.remotes.origin.pull.side_effect = pull_side_effect
        mock_git_repo.iter_commits.return_value = [MagicMock(), MagicMock()]

        with patch("code_intel_mcp.git_manager.git.Repo", return_value=mock_git_repo):
            result = await git_manager.pull("my-repo")

        assert result.updated is True
        assert result.new_commits == 2
        # Re-indexing triggered
        git_manager.zoekt.index_repo.assert_awaited()

    @pytest.mark.asyncio
    async def test_pull_no_changes(self, git_manager: GitManager, tmp_repo_store: Path):
        """Pull with no new commits does not trigger re-indexing."""
        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        mock_git_repo = MagicMock()
        mock_commit = MagicMock()
        mock_commit.hexsha = "same-sha"
        mock_git_repo.head.commit = mock_commit

        with patch("code_intel_mcp.git_manager.git.Repo", return_value=mock_git_repo):
            result = await git_manager.pull("my-repo")

        assert result.updated is False
        assert result.new_commits == 0
        git_manager.zoekt.index_repo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pull_unknown_repo_raises(self, git_manager: GitManager):
        with pytest.raises(RepoNotFoundError):
            await git_manager.pull("nonexistent")

    @pytest.mark.asyncio
    async def test_pull_git_error_raises(self, git_manager: GitManager, tmp_repo_store: Path):
        import git.exc

        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        mock_git_repo = MagicMock()
        mock_git_repo.head.commit.hexsha = "aaa"
        mock_git_repo.remotes.origin.pull.side_effect = git.exc.GitCommandError(
            "pull", "fatal: network error"
        )

        with patch("code_intel_mcp.git_manager.git.Repo", return_value=mock_git_repo):
            with pytest.raises(GitOperationError, match="my-repo"):
                await git_manager.pull("my-repo")


# ------------------------------------------------------------------
# GitManager.checkout
# ------------------------------------------------------------------


class TestCheckout:
    @pytest.mark.asyncio
    async def test_checkout_branch(self, git_manager: GitManager, tmp_repo_store: Path):
        """Checkout updates registry current_ref and triggers re-indexing."""
        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        mock_git_repo = MagicMock()
        mock_git_repo.head.is_detached = False
        mock_git_repo.active_branch.name = "develop"

        with patch("code_intel_mcp.git_manager.git.Repo", return_value=mock_git_repo):
            result = await git_manager.checkout("my-repo", "develop")

        assert result.previous_ref == "main"
        assert result.new_ref == "develop"
        mock_git_repo.git.checkout.assert_called_once_with("develop")
        git_manager.zoekt.index_repo.assert_awaited()

        # Registry updated
        updated = git_manager.registry.get("my-repo")
        assert updated is not None
        assert updated.current_ref == "develop"

    @pytest.mark.asyncio
    async def test_checkout_detached_sha(self, git_manager: GitManager, tmp_repo_store: Path):
        """Checkout a SHA results in detached HEAD with hexsha as current_ref."""
        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        mock_git_repo = MagicMock()
        mock_git_repo.head.is_detached = True
        mock_git_repo.head.commit.hexsha = "abc123def456"

        with patch("code_intel_mcp.git_manager.git.Repo", return_value=mock_git_repo):
            result = await git_manager.checkout("my-repo", "abc123def456")

        assert result.new_ref == "abc123def456"

    @pytest.mark.asyncio
    async def test_checkout_unknown_repo_raises(self, git_manager: GitManager):
        with pytest.raises(RepoNotFoundError):
            await git_manager.checkout("nonexistent", "main")

    @pytest.mark.asyncio
    async def test_checkout_git_error_raises(self, git_manager: GitManager, tmp_repo_store: Path):
        import git.exc

        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        mock_git_repo = MagicMock()
        mock_git_repo.git.checkout.side_effect = git.exc.GitCommandError(
            "checkout", "error: pathspec 'bad-ref' did not match"
        )

        with patch("code_intel_mcp.git_manager.git.Repo", return_value=mock_git_repo):
            with pytest.raises(GitOperationError, match="my-repo"):
                await git_manager.checkout("my-repo", "bad-ref")


# ------------------------------------------------------------------
# GitManager.sync_all
# ------------------------------------------------------------------


class TestSyncAll:
    @pytest.mark.asyncio
    async def test_sync_all_pulls_every_repo(self, git_manager: GitManager, tmp_repo_store: Path):
        """sync_all returns one result per managed repo."""
        for name in ["repo-a", "repo-b", "repo-c"]:
            repo_dir = tmp_repo_store / name
            repo_dir.mkdir()
            managed = ManagedRepo(
                name=name,
                git_url=f"https://github.com/org/{name}.git",
                local_path=repo_dir,
                current_ref="main",
            )
            git_manager.registry.add(managed)

        mock_git_repo = MagicMock()
        mock_commit = MagicMock()
        mock_commit.hexsha = "same"
        mock_git_repo.head.commit = mock_commit

        with patch("code_intel_mcp.git_manager.git.Repo", return_value=mock_git_repo):
            results = await git_manager.sync_all()

        assert len(results) == 3
        assert all(r.success for r in results)
        names = {r.repo_name for r in results}
        assert names == {"repo-a", "repo-b", "repo-c"}

    @pytest.mark.asyncio
    async def test_sync_all_continues_on_failure(self, git_manager: GitManager, tmp_repo_store: Path):
        """sync_all continues when one repo fails, reporting per-repo results."""
        import git.exc

        for name in ["good-repo", "bad-repo"]:
            repo_dir = tmp_repo_store / name
            repo_dir.mkdir()
            managed = ManagedRepo(
                name=name,
                git_url=f"https://github.com/org/{name}.git",
                local_path=repo_dir,
                current_ref="main",
            )
            git_manager.registry.add(managed)

        mock_good = MagicMock()
        mock_commit = MagicMock()
        mock_commit.hexsha = "same"
        mock_good.head.commit = mock_commit

        mock_bad = MagicMock()
        mock_bad.head.commit.hexsha = "aaa"
        mock_bad.remotes.origin.pull.side_effect = git.exc.GitCommandError(
            "pull", "fatal: error"
        )

        def repo_factory(path):
            if "bad-repo" in str(path):
                return mock_bad
            return mock_good

        with patch("code_intel_mcp.git_manager.git.Repo", side_effect=repo_factory):
            results = await git_manager.sync_all()

        assert len(results) == 2
        success_results = [r for r in results if r.success]
        failure_results = [r for r in results if not r.success]
        assert len(success_results) == 1
        assert len(failure_results) == 1
        assert failure_results[0].error is not None


# ------------------------------------------------------------------
# GitManager.remove
# ------------------------------------------------------------------


class TestRemove:
    @pytest.mark.asyncio
    async def test_remove_cleans_all(self, git_manager: GitManager, tmp_repo_store: Path):
        """Remove deletes directory, registry entry, and zoekt index."""
        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()
        (repo_dir / "file.txt").write_text("content")

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        await git_manager.remove("my-repo")

        assert not repo_dir.exists()
        assert git_manager.registry.get("my-repo") is None
        git_manager.zoekt.remove_index.assert_awaited_once_with("my-repo")

    @pytest.mark.asyncio
    async def test_remove_unknown_repo_raises(self, git_manager: GitManager):
        with pytest.raises(RepoNotFoundError):
            await git_manager.remove("nonexistent")


# ------------------------------------------------------------------
# Helper methods
# ------------------------------------------------------------------


class TestHelpers:
    def test_get_branches(self, git_manager: GitManager, tmp_repo_store: Path):
        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        mock_branch_a = MagicMock()
        mock_branch_a.name = "main"
        mock_branch_b = MagicMock()
        mock_branch_b.name = "develop"

        mock_git_repo = MagicMock()
        mock_git_repo.branches = [mock_branch_a, mock_branch_b]

        with patch("code_intel_mcp.git_manager.git.Repo", return_value=mock_git_repo):
            branches = git_manager.get_branches("my-repo")

        assert branches == ["main", "develop"]

    def test_get_tags(self, git_manager: GitManager, tmp_repo_store: Path):
        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        mock_tag = MagicMock()
        mock_tag.name = "v1.0.0"
        mock_git_repo = MagicMock()
        mock_git_repo.tags = [mock_tag]

        with patch("code_intel_mcp.git_manager.git.Repo", return_value=mock_git_repo):
            tags = git_manager.get_tags("my-repo")

        assert tags == ["v1.0.0"]

    def test_get_last_commit(self, git_manager: GitManager, tmp_repo_store: Path):
        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        mock_commit = MagicMock()
        mock_commit.hexsha = "abc123"
        mock_commit.author = "Test Author"
        mock_commit.message = "Initial commit\n"
        mock_commit.committed_datetime = ts

        mock_git_repo = MagicMock()
        mock_git_repo.head.commit = mock_commit

        with patch("code_intel_mcp.git_manager.git.Repo", return_value=mock_git_repo):
            info = git_manager.get_last_commit("my-repo")

        assert info.sha == "abc123"
        assert info.author == "Test Author"
        assert info.message == "Initial commit"
        assert info.timestamp == ts

    def test_get_disk_size(self, git_manager: GitManager, tmp_repo_store: Path):
        repo_dir = tmp_repo_store / "my-repo"
        repo_dir.mkdir()
        (repo_dir / "a.txt").write_text("hello")
        (repo_dir / "b.txt").write_text("world!")

        managed = ManagedRepo(
            name="my-repo",
            git_url="https://github.com/org/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
        )
        git_manager.registry.add(managed)

        size = git_manager.get_disk_size("my-repo")
        assert size == 5 + 6  # "hello" + "world!"

    def test_get_disk_size_missing_dir(self, git_manager: GitManager, tmp_repo_store: Path):
        managed = ManagedRepo(
            name="gone-repo",
            git_url="https://github.com/org/gone-repo.git",
            local_path=tmp_repo_store / "gone-repo",
            current_ref="main",
        )
        git_manager.registry.add(managed)

        assert git_manager.get_disk_size("gone-repo") == 0

    def test_helpers_unknown_repo_raises(self, git_manager: GitManager):
        with pytest.raises(RepoNotFoundError):
            git_manager.get_branches("nope")
        with pytest.raises(RepoNotFoundError):
            git_manager.get_tags("nope")
        with pytest.raises(RepoNotFoundError):
            git_manager.get_last_commit("nope")
        with pytest.raises(RepoNotFoundError):
            git_manager.get_disk_size("nope")


# ------------------------------------------------------------------
# GitManager.clone_gitlab_group
# ------------------------------------------------------------------


class TestCloneGitlabGroup:
    @pytest.mark.asyncio
    async def test_clones_all_projects(self, git_manager: GitManager):
        """All projects from a group are cloned and registered."""
        from code_intel_mcp.models import GitLabProject

        projects = [
            GitLabProject(
                name="alpha",
                path_with_namespace="group/alpha",
                http_url_to_repo="https://gitlab.example.com/group/alpha.git",
                ssh_url_to_repo="git@gitlab.example.com:group/alpha.git",
            ),
            GitLabProject(
                name="beta",
                path_with_namespace="group/beta",
                http_url_to_repo="https://gitlab.example.com/group/beta.git",
                ssh_url_to_repo="git@gitlab.example.com:group/beta.git",
            ),
        ]

        mock_client = AsyncMock()
        mock_client.list_group_projects = AsyncMock(return_value=projects)

        mock_git_repo = MagicMock()
        mock_git_repo.active_branch.name = "main"
        mock_git_repo.head.is_detached = False

        with patch("code_intel_mcp.git_manager.git.Repo.clone_from", return_value=mock_git_repo):
            result = await git_manager.clone_gitlab_group("group", client=mock_client)

        assert set(result.cloned) == {"group/alpha", "group/beta"}
        assert result.skipped == []
        assert result.failed == []
        assert result.group_path == "group"
        mock_client.list_group_projects.assert_awaited_once_with("group")

    @pytest.mark.asyncio
    async def test_skips_already_managed(self, git_manager: GitManager, tmp_repo_store: Path):
        """Projects whose URL is already registered are skipped."""
        from code_intel_mcp.models import GitLabProject

        # Pre-register one repo
        existing = ManagedRepo(
            name="alpha",
            git_url="https://gitlab.example.com/group/alpha.git",
            local_path=tmp_repo_store / "alpha",
            current_ref="main",
        )
        git_manager.registry.add(existing)

        projects = [
            GitLabProject(
                name="alpha",
                path_with_namespace="group/alpha",
                http_url_to_repo="https://gitlab.example.com/group/alpha.git",
                ssh_url_to_repo="git@gitlab.example.com:group/alpha.git",
            ),
            GitLabProject(
                name="gamma",
                path_with_namespace="group/gamma",
                http_url_to_repo="https://gitlab.example.com/group/gamma.git",
                ssh_url_to_repo="git@gitlab.example.com:group/gamma.git",
            ),
        ]

        mock_client = AsyncMock()
        mock_client.list_group_projects = AsyncMock(return_value=projects)

        mock_git_repo = MagicMock()
        mock_git_repo.active_branch.name = "main"
        mock_git_repo.head.is_detached = False

        with patch("code_intel_mcp.git_manager.git.Repo.clone_from", return_value=mock_git_repo):
            result = await git_manager.clone_gitlab_group("group", client=mock_client)

        assert result.cloned == ["group/gamma"]
        assert result.skipped == ["group/alpha"]
        assert result.failed == []

    @pytest.mark.asyncio
    async def test_gitlab_api_failure_raises(self, git_manager: GitManager):
        """GitLab API errors propagate with group path context."""
        from code_intel_mcp.errors import GitLabNotFoundError

        mock_client = AsyncMock()
        mock_client.list_group_projects = AsyncMock(
            side_effect=GitLabNotFoundError(
                "GitLab group not found: 'bad/group'",
                details={"group_path": "bad/group"},
            )
        )

        with pytest.raises(GitLabNotFoundError, match="bad/group"):
            await git_manager.clone_gitlab_group("bad/group", client=mock_client)

    @pytest.mark.asyncio
    async def test_individual_clone_failure_continues(self, git_manager: GitManager):
        """If one project fails to clone, the rest still proceed."""
        import git.exc

        from code_intel_mcp.models import GitLabProject

        projects = [
            GitLabProject(
                name="good",
                path_with_namespace="group/good",
                http_url_to_repo="https://gitlab.example.com/group/good.git",
                ssh_url_to_repo="git@gitlab.example.com:group/good.git",
            ),
            GitLabProject(
                name="bad",
                path_with_namespace="group/bad",
                http_url_to_repo="https://gitlab.example.com/group/bad.git",
                ssh_url_to_repo="git@gitlab.example.com:group/bad.git",
            ),
        ]

        mock_client = AsyncMock()
        mock_client.list_group_projects = AsyncMock(return_value=projects)

        mock_git_repo = MagicMock()
        mock_git_repo.active_branch.name = "main"
        mock_git_repo.head.is_detached = False

        call_count = 0

        def clone_side_effect(url, path):
            nonlocal call_count
            call_count += 1
            if "bad" in url:
                raise git.exc.GitCommandError("clone", "fatal: repo not found")
            return mock_git_repo

        with patch("code_intel_mcp.git_manager.git.Repo.clone_from", side_effect=clone_side_effect):
            result = await git_manager.clone_gitlab_group("group", client=mock_client)

        assert result.cloned == ["group/good"]
        assert len(result.failed) == 1
        assert result.failed[0]["project"] == "group/bad"
        assert "bad" in result.failed[0]["error"]

    @pytest.mark.asyncio
    async def test_empty_group(self, git_manager: GitManager):
        """An empty group returns an empty result."""
        mock_client = AsyncMock()
        mock_client.list_group_projects = AsyncMock(return_value=[])

        result = await git_manager.clone_gitlab_group("empty-group", client=mock_client)

        assert result.cloned == []
        assert result.skipped == []
        assert result.failed == []

    @pytest.mark.asyncio
    async def test_indexes_newly_cloned_repos(self, git_manager: GitManager):
        """Each newly cloned repo triggers Zoekt indexing."""
        from code_intel_mcp.models import GitLabProject

        projects = [
            GitLabProject(
                name="repo1",
                path_with_namespace="group/repo1",
                http_url_to_repo="https://gitlab.example.com/group/repo1.git",
                ssh_url_to_repo="git@gitlab.example.com:group/repo1.git",
            ),
        ]

        mock_client = AsyncMock()
        mock_client.list_group_projects = AsyncMock(return_value=projects)

        mock_git_repo = MagicMock()
        mock_git_repo.active_branch.name = "main"
        mock_git_repo.head.is_detached = False

        with patch("code_intel_mcp.git_manager.git.Repo.clone_from", return_value=mock_git_repo):
            await git_manager.clone_gitlab_group("group", client=mock_client)

        # clone() internally calls zoekt.index_repo
        git_manager.zoekt.index_repo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_from_env_when_no_client(self, git_manager: GitManager):
        """When no client is passed, GitLabClient.from_env() is used."""
        from code_intel_mcp.errors import GitLabAuthError

        # No GITLAB_URL / GITLAB_TOKEN set → from_env() should raise
        with patch.dict(os.environ, {}, clear=True), pytest.raises(GitLabAuthError, match="GITLAB_URL"):
            await git_manager.clone_gitlab_group("some/group")
