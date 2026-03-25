"""Unit tests for the Registry class."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from code_intel_mcp.errors import RepoAlreadyExistsError, RepoNotFoundError
from code_intel_mcp.models import IndexStatus, ManagedRepo
from code_intel_mcp.registry import Registry


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_repo(
    name: str = "my-repo",
    git_url: str = "https://gitlab.example.com/group/my-repo.git",
    local_path: str = "~/.code-intel-mcp/repos/my-repo",
    current_ref: str = "main",
    last_pull: datetime | None = None,
    index_status: IndexStatus = IndexStatus.MISSING,
) -> ManagedRepo:
    return ManagedRepo(
        name=name,
        git_url=git_url,
        local_path=Path(local_path),
        current_ref=current_ref,
        last_pull=last_pull,
        index_status=index_status,
    )


# ------------------------------------------------------------------
# load / save
# ------------------------------------------------------------------

class TestLoadSave:
    def test_load_missing_file_initialises_empty(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        reg.load()
        assert reg.list_all() == []

    def test_load_valid_json(self, tmp_config_path: Path) -> None:
        data = {
            "version": 1,
            "repos": [
                {
                    "name": "r1",
                    "git_url": "https://example.com/r1.git",
                    "local_path": "/tmp/r1",
                    "current_ref": "main",
                    "last_pull": "2024-01-15T10:30:00+00:00",
                    "index_status": "current",
                }
            ],
        }
        tmp_config_path.write_text(json.dumps(data))
        reg = Registry(tmp_config_path)
        reg.load()
        repos = reg.list_all()
        assert len(repos) == 1
        assert repos[0].name == "r1"
        assert repos[0].index_status == IndexStatus.CURRENT

    def test_load_corrupt_json_initialises_empty(self, tmp_config_path: Path) -> None:
        tmp_config_path.write_text("{not valid json!!!")
        reg = Registry(tmp_config_path)
        reg.load()
        assert reg.list_all() == []

    def test_load_invalid_structure_initialises_empty(self, tmp_config_path: Path) -> None:
        tmp_config_path.write_text(json.dumps({"foo": "bar"}))
        reg = Registry(tmp_config_path)
        reg.load()
        assert reg.list_all() == []

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        config = tmp_path / "nested" / "dir" / "config.json"
        reg = Registry(config)
        reg.save()
        assert config.exists()
        data = json.loads(config.read_text())
        assert data["version"] == 1
        assert data["repos"] == []

    def test_save_round_trip(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        repo = _make_repo(last_pull=datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc))
        reg.add(repo)
        reg.save()

        reg2 = Registry(tmp_config_path)
        reg2.load()
        repos = reg2.list_all()
        assert len(repos) == 1
        assert repos[0].name == "my-repo"
        assert repos[0].git_url == repo.git_url
        assert repos[0].current_ref == "main"

    def test_save_sorted_keys_and_indent(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        reg.add(_make_repo(name="b-repo", git_url="https://example.com/b.git"))
        reg.add(_make_repo(name="a-repo", git_url="https://example.com/a.git"))
        reg.save()

        raw = tmp_config_path.read_text()
        data = json.loads(raw)
        # Top-level keys sorted
        assert list(data.keys()) == ["repos", "version"]
        # Each repo entry has sorted keys
        for entry in data["repos"]:
            assert list(entry.keys()) == sorted(entry.keys())
        # 2-space indentation
        assert '  "repos"' in raw


# ------------------------------------------------------------------
# add / remove / get / list_all / update
# ------------------------------------------------------------------

class TestCRUD:
    def test_add_and_get(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        repo = _make_repo()
        reg.add(repo)
        assert reg.get("my-repo") is repo

    def test_add_duplicate_raises(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        reg.add(_make_repo())
        with pytest.raises(RepoAlreadyExistsError):
            reg.add(_make_repo())

    def test_remove_existing(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        reg.add(_make_repo())
        reg.remove("my-repo")
        assert reg.get("my-repo") is None

    def test_remove_missing_raises(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        with pytest.raises(RepoNotFoundError):
            reg.remove("nonexistent")

    def test_get_missing_returns_none(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        assert reg.get("nope") is None

    def test_list_all(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        reg.add(_make_repo(name="a"))
        reg.add(_make_repo(name="b"))
        names = {r.name for r in reg.list_all()}
        assert names == {"a", "b"}

    def test_update_existing(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        reg.add(_make_repo())
        reg.update("my-repo", current_ref="develop")
        assert reg.get("my-repo").current_ref == "develop"  # type: ignore[union-attr]

    def test_update_missing_raises(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        with pytest.raises(RepoNotFoundError):
            reg.update("ghost", current_ref="x")

    def test_update_invalid_field_raises(self, tmp_config_path: Path) -> None:
        reg = Registry(tmp_config_path)
        reg.add(_make_repo())
        with pytest.raises(ValueError, match="no field"):
            reg.update("my-repo", nonexistent_field="x")


# ------------------------------------------------------------------
# validate_paths
# ------------------------------------------------------------------

class TestValidatePaths:
    def test_existing_paths_not_reported(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repos" / "my-repo"
        repo_dir.mkdir(parents=True)
        reg = Registry(tmp_path / "config.json")
        reg.add(_make_repo(local_path=str(repo_dir)))
        assert reg.validate_paths() == []

    def test_missing_paths_reported(self, tmp_path: Path) -> None:
        reg = Registry(tmp_path / "config.json")
        reg.add(_make_repo(name="gone", local_path="/nonexistent/path/gone"))
        missing = reg.validate_paths()
        assert missing == ["gone"]

    def test_mixed_paths(self, tmp_path: Path) -> None:
        existing_dir = tmp_path / "repos" / "exists"
        existing_dir.mkdir(parents=True)
        reg = Registry(tmp_path / "config.json")
        reg.add(_make_repo(name="exists", local_path=str(existing_dir)))
        reg.add(_make_repo(name="gone", local_path="/nonexistent/path/gone"))
        missing = reg.validate_paths()
        assert missing == ["gone"]


# ------------------------------------------------------------------
# serialize / deserialize
# ------------------------------------------------------------------

class TestSerialization:
    def test_serialize_produces_valid_json(self) -> None:
        repos = [_make_repo()]
        result = Registry.serialize(repos)
        data = json.loads(result)
        assert data["version"] == 1
        assert len(data["repos"]) == 1

    def test_deserialize_reconstructs_repos(self) -> None:
        original = _make_repo(
            last_pull=datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc),
            index_status=IndexStatus.CURRENT,
        )
        json_str = Registry.serialize([original])
        repos = Registry.deserialize(json_str)
        assert len(repos) == 1
        r = repos[0]
        assert r.name == original.name
        assert r.git_url == original.git_url
        assert r.current_ref == original.current_ref
        assert r.index_status == IndexStatus.CURRENT

    def test_deserialize_none_last_pull(self) -> None:
        repo = _make_repo(last_pull=None)
        json_str = Registry.serialize([repo])
        repos = Registry.deserialize(json_str)
        assert repos[0].last_pull is None

    def test_serialize_sorted_keys(self) -> None:
        repos = [_make_repo()]
        result = Registry.serialize(repos)
        data = json.loads(result)
        assert list(data.keys()) == sorted(data.keys())
        for entry in data["repos"]:
            assert list(entry.keys()) == sorted(entry.keys())
