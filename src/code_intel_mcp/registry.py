"""JSON-based persistence for repository metadata."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from code_intel_mcp.errors import RepoAlreadyExistsError, RepoNotFoundError
from code_intel_mcp.models import IndexStatus, ManagedRepo

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".code-intel-mcp" / "config.json"

_EMPTY_STATE: dict = {"version": 1, "repos": []}


class Registry:
    """Manages the JSON registry of tracked repositories."""

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self._config_path = config_path
        self._repos: dict[str, ManagedRepo] = {}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load registry from disk.

        On missing file: initialise empty.
        On corrupt / invalid JSON: log warning and initialise empty.
        """
        if not self._config_path.exists():
            self._repos = {}
            return

        raw = self._config_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "Registry file %s contains invalid JSON – initialising empty registry.",
                self._config_path,
            )
            self._repos = {}
            return

        if not isinstance(data, dict) or "repos" not in data or "version" not in data:
            logger.warning(
                "Registry file %s has invalid structure – initialising empty registry.",
                self._config_path,
            )
            self._repos = {}
            return

        try:
            repos = Registry.deserialize(json.dumps(data))
            self._repos = {r.name: r for r in repos}
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "Registry file %s has invalid repo entries – initialising empty registry.",
                self._config_path,
            )
            self._repos = {}

    def save(self) -> None:
        """Persist current state to disk (sorted keys, 2-space indent)."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        json_str = Registry.serialize(list(self._repos.values()))
        self._config_path.write_text(json_str, encoding="utf-8")

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    def add(self, repo: ManagedRepo) -> None:
        """Register a new repo. Raises *RepoAlreadyExistsError* on duplicate."""
        if repo.name in self._repos:
            raise RepoAlreadyExistsError(
                f"Repository '{repo.name}' is already managed.",
                details={"repo_name": repo.name},
            )
        self._repos[repo.name] = repo

    def remove(self, repo_name: str) -> None:
        """Remove a repo by name. Raises *RepoNotFoundError* if absent."""
        if repo_name not in self._repos:
            raise RepoNotFoundError(
                f"Repository '{repo_name}' is not managed.",
                details={"repo_name": repo_name},
            )
        del self._repos[repo_name]

    def get(self, repo_name: str) -> ManagedRepo | None:
        """Return the repo with *repo_name*, or ``None``."""
        return self._repos.get(repo_name)

    def list_all(self) -> list[ManagedRepo]:
        """Return every registered repo."""
        return list(self._repos.values())

    def update(self, repo_name: str, **fields: object) -> None:
        """Update fields on an existing repo. Raises *RepoNotFoundError* if absent."""
        repo = self._repos.get(repo_name)
        if repo is None:
            raise RepoNotFoundError(
                f"Repository '{repo_name}' is not managed.",
                details={"repo_name": repo_name},
            )
        for key, value in fields.items():
            if not hasattr(repo, key):
                raise ValueError(f"ManagedRepo has no field '{key}'")
            setattr(repo, key, value)

    # ------------------------------------------------------------------
    # Path validation
    # ------------------------------------------------------------------

    def validate_paths(self) -> list[str]:
        """Return names of repos whose *local_path* does not exist on disk."""
        missing: list[str] = []
        for repo in self._repos.values():
            path = Path(str(repo.local_path)).expanduser()
            if not path.exists():
                missing.append(repo.name)
        return missing

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def serialize(repos: list[ManagedRepo]) -> str:
        """Serialize a list of repos to a JSON string (sorted keys, 2-space indent)."""
        repo_dicts = []
        for r in repos:
            entry: dict[str, object] = {
                "current_ref": r.current_ref,
                "git_url": r.git_url,
                "index_status": r.index_status.value,
                "last_pull": r.last_pull.isoformat() if r.last_pull is not None else None,
                "local_path": str(r.local_path),
                "name": r.name,
            }
            repo_dicts.append(entry)

        state = {"repos": repo_dicts, "version": 1}
        return json.dumps(state, indent=2, sort_keys=True) + "\n"

    @staticmethod
    def deserialize(json_str: str) -> list[ManagedRepo]:
        """Deserialize a JSON string into a list of *ManagedRepo* objects."""
        data = json.loads(json_str)
        repos: list[ManagedRepo] = []
        for entry in data.get("repos", []):
            last_pull_raw = entry.get("last_pull")
            if last_pull_raw is not None:
                last_pull = datetime.fromisoformat(last_pull_raw)
            else:
                last_pull = None

            repos.append(
                ManagedRepo(
                    name=entry["name"],
                    git_url=entry["git_url"],
                    local_path=Path(entry["local_path"]),
                    current_ref=entry["current_ref"],
                    last_pull=last_pull,
                    index_status=IndexStatus(entry.get("index_status", "missing")),
                )
            )
        return repos
