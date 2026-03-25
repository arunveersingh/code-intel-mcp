"""Exception hierarchy for code-intel-mcp."""

from __future__ import annotations


class CodeIntelError(Exception):
    """Base error for all code-intel-mcp errors."""

    def __init__(self, message: str, details: dict | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


class RepoNotFoundError(CodeIntelError):
    """Raised when a referenced repo is not in the Registry."""

    pass


class RepoAlreadyExistsError(CodeIntelError):
    """Raised when attempting to add a repo that is already managed."""

    pass


class GitOperationError(CodeIntelError):
    """Raised when a git operation (clone, pull, checkout) fails."""

    pass


class GitLabError(CodeIntelError):
    """Base for GitLab API errors."""

    pass


class GitLabAuthError(GitLabError):
    """Raised when GitLab authentication fails or env vars are missing."""

    pass


class GitLabNotFoundError(GitLabError):
    """Raised when a GitLab group or project is not found."""

    pass


class SearchEngineUnavailableError(CodeIntelError):
    """Raised when the Zoekt webserver is not running or unreachable."""

    pass


class CodeIntelFileNotFoundError(CodeIntelError):
    """Raised when a requested file or directory path does not exist.

    Namespaced to avoid shadowing the builtin FileNotFoundError.
    """

    pass


class BinaryNotFoundError(CodeIntelError):
    """Raised when required binaries (zoekt-index, zoekt-webserver, git) are missing."""

    pass
