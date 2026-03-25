"""Tests for the FileBrowser class."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_intel_mcp.errors import CodeIntelFileNotFoundError, RepoNotFoundError
from code_intel_mcp.files import FileBrowser
from code_intel_mcp.models import IndexStatus, ManagedRepo
from code_intel_mcp.registry import Registry


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def repo_store(tmp_path: Path) -> Path:
    """Return a temporary repo store directory."""
    store = tmp_path / "repos"
    store.mkdir()
    return store


@pytest.fixture()
def registry(tmp_path: Path) -> Registry:
    """Return a fresh in-memory Registry."""
    reg = Registry(config_path=tmp_path / "config.json")
    return reg


@pytest.fixture()
def sample_repo(repo_store: Path, registry: Registry) -> Path:
    """Create a minimal repo directory and register it."""
    repo_dir = repo_store / "my-repo"
    repo_dir.mkdir()
    (repo_dir / "hello.txt").write_text("hello world", encoding="utf-8")
    (repo_dir / "sub").mkdir()
    (repo_dir / "sub" / "nested.txt").write_text("nested", encoding="utf-8")
    (repo_dir / "README.md").write_text("# My Repo", encoding="utf-8")

    registry.add(
        ManagedRepo(
            name="my-repo",
            git_url="https://example.com/my-repo.git",
            local_path=repo_dir,
            current_ref="main",
            index_status=IndexStatus.CURRENT,
        )
    )
    return repo_dir


@pytest.fixture()
def browser(repo_store: Path, registry: Registry) -> FileBrowser:
    return FileBrowser(repo_store=repo_store, registry=registry)


# ------------------------------------------------------------------
# read_file
# ------------------------------------------------------------------


class TestReadFile:
    def test_read_existing_file(self, browser: FileBrowser, sample_repo: Path) -> None:
        content = browser.read_file("my-repo", "hello.txt")
        assert content == "hello world"

    def test_read_nested_file(self, browser: FileBrowser, sample_repo: Path) -> None:
        content = browser.read_file("my-repo", "sub/nested.txt")
        assert content == "nested"

    def test_read_nonexistent_file_raises(self, browser: FileBrowser, sample_repo: Path) -> None:
        with pytest.raises(CodeIntelFileNotFoundError):
            browser.read_file("my-repo", "does_not_exist.txt")

    def test_read_unknown_repo_raises(self, browser: FileBrowser) -> None:
        with pytest.raises(RepoNotFoundError):
            browser.read_file("no-such-repo", "file.txt")

    def test_path_traversal_rejected(self, browser: FileBrowser, sample_repo: Path) -> None:
        with pytest.raises(CodeIntelFileNotFoundError):
            browser.read_file("my-repo", "../../etc/passwd")

    def test_path_traversal_with_dotdot_in_middle(self, browser: FileBrowser, sample_repo: Path) -> None:
        with pytest.raises(CodeIntelFileNotFoundError):
            browser.read_file("my-repo", "sub/../../../../../../etc/passwd")


# ------------------------------------------------------------------
# list_directory
# ------------------------------------------------------------------


class TestListDirectory:
    def test_list_root(self, browser: FileBrowser, sample_repo: Path) -> None:
        entries = browser.list_directory("my-repo")
        names = {e.name for e in entries}
        assert "hello.txt" in names
        assert "sub" in names
        assert "README.md" in names

    def test_list_subdirectory(self, browser: FileBrowser, sample_repo: Path) -> None:
        entries = browser.list_directory("my-repo", "sub")
        assert len(entries) == 1
        assert entries[0].name == "nested.txt"
        assert entries[0].is_directory is False
        assert entries[0].size is not None

    def test_directory_entry_flags(self, browser: FileBrowser, sample_repo: Path) -> None:
        entries = browser.list_directory("my-repo")
        by_name = {e.name: e for e in entries}
        assert by_name["sub"].is_directory is True
        assert by_name["sub"].size is None
        assert by_name["hello.txt"].is_directory is False

    def test_nonexistent_dir_raises(self, browser: FileBrowser, sample_repo: Path) -> None:
        with pytest.raises(CodeIntelFileNotFoundError):
            browser.list_directory("my-repo", "no_such_dir")

    def test_unknown_repo_raises(self, browser: FileBrowser) -> None:
        with pytest.raises(RepoNotFoundError):
            browser.list_directory("no-such-repo")

    def test_path_traversal_rejected(self, browser: FileBrowser, sample_repo: Path) -> None:
        with pytest.raises(CodeIntelFileNotFoundError):
            browser.list_directory("my-repo", "../..")


# ------------------------------------------------------------------
# get_repo_overview
# ------------------------------------------------------------------


class TestGetRepoOverview:
    def test_overview_with_readme(self, browser: FileBrowser, sample_repo: Path) -> None:
        overview = browser.get_repo_overview("my-repo")
        assert overview.repo_name == "my-repo"
        assert overview.readme_content == "# My Repo"
        assert len(overview.directory_listing) > 0

    def test_overview_without_readme(
        self, browser: FileBrowser, sample_repo: Path
    ) -> None:
        (sample_repo / "README.md").unlink()
        overview = browser.get_repo_overview("my-repo")
        assert overview.readme_content is None

    def test_overview_readme_rst_fallback(
        self, browser: FileBrowser, sample_repo: Path
    ) -> None:
        (sample_repo / "README.md").unlink()
        (sample_repo / "README.rst").write_text("RST readme", encoding="utf-8")
        overview = browser.get_repo_overview("my-repo")
        assert overview.readme_content == "RST readme"

    def test_overview_readme_plain_fallback(
        self, browser: FileBrowser, sample_repo: Path
    ) -> None:
        (sample_repo / "README.md").unlink()
        (sample_repo / "README").write_text("plain readme", encoding="utf-8")
        overview = browser.get_repo_overview("my-repo")
        assert overview.readme_content == "plain readme"

    def test_overview_build_summary_with_pom(
        self, browser: FileBrowser, sample_repo: Path
    ) -> None:
        (sample_repo / "pom.xml").write_text("<project/>", encoding="utf-8")
        overview = browser.get_repo_overview("my-repo")
        assert overview.build_summary is not None
        assert "pom.xml" in overview.build_summary

    def test_overview_build_summary_with_package_json(
        self, browser: FileBrowser, sample_repo: Path
    ) -> None:
        (sample_repo / "package.json").write_text("{}", encoding="utf-8")
        overview = browser.get_repo_overview("my-repo")
        assert overview.build_summary is not None
        assert "package.json" in overview.build_summary

    def test_overview_no_build_config(
        self, browser: FileBrowser, sample_repo: Path
    ) -> None:
        overview = browser.get_repo_overview("my-repo")
        assert overview.build_summary is None

    def test_overview_unknown_repo_raises(self, browser: FileBrowser) -> None:
        with pytest.raises(RepoNotFoundError):
            browser.get_repo_overview("no-such-repo")
