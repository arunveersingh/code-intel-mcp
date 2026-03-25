"""GitLab REST API client for code-intel-mcp."""

from __future__ import annotations

import os
from urllib.parse import quote as url_quote

import httpx

from code_intel_mcp.errors import GitLabAuthError, GitLabError, GitLabNotFoundError
from code_intel_mcp.models import GitLabProject


class GitLabClient:
    """Async client for the GitLab REST API.

    Uses ``httpx.AsyncClient`` for all HTTP communication and includes
    the ``PRIVATE-TOKEN`` header in every request.
    """

    def __init__(self, gitlab_url: str, gitlab_token: str) -> None:
        self._base_url = gitlab_url.rstrip("/")
        self._token = gitlab_token

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> GitLabClient:
        """Create a client from ``GITLAB_URL`` and ``GITLAB_TOKEN`` env vars.

        Raises ``GitLabAuthError`` naming every missing variable.
        """
        missing: list[str] = []
        gitlab_url = os.environ.get("GITLAB_URL")
        gitlab_token = os.environ.get("GITLAB_TOKEN")

        if not gitlab_url:
            missing.append("GITLAB_URL")
        if not gitlab_token:
            missing.append("GITLAB_TOKEN")

        if missing:
            names = ", ".join(missing)
            raise GitLabAuthError(
                f"Missing required environment variable(s): {names}",
                details={"missing_vars": missing},
            )

        return cls(gitlab_url, gitlab_token)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_group_projects(self, group_path: str) -> list[GitLabProject]:
        """Return every project in *group_path*, paginating automatically."""
        encoded_path = url_quote(group_path, safe="")
        url = f"{self._base_url}/api/v4/groups/{encoded_path}/projects"

        projects: list[GitLabProject] = []
        page = 1
        per_page = 100

        async with httpx.AsyncClient() as client:
            while True:
                response = await self._get(
                    client,
                    url,
                    params={"page": page, "per_page": per_page, "include_subgroups": "false"},
                    group_path=group_path,
                )
                data = response.json()
                for item in data:
                    projects.append(
                        GitLabProject(
                            name=item["name"],
                            path_with_namespace=item["path_with_namespace"],
                            http_url_to_repo=item["http_url_to_repo"],
                            ssh_url_to_repo=item["ssh_url_to_repo"],
                        )
                    )

                # GitLab signals the next page via the x-next-page header.
                next_page = response.headers.get("x-next-page", "")
                if not next_page:
                    break
                page = int(next_page)

        return projects

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self._token}

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict | None = None,
        group_path: str = "",
    ) -> httpx.Response:
        """Issue a GET request with error mapping."""
        try:
            response = await client.get(url, headers=self._headers(), params=params)
        except httpx.ConnectError as exc:
            raise GitLabError(
                f"Network error connecting to GitLab: {exc}",
                details={"group_path": group_path},
            ) from exc
        except httpx.HTTPError as exc:
            raise GitLabError(
                f"HTTP error communicating with GitLab: {exc}",
                details={"group_path": group_path},
            ) from exc

        if response.status_code in (401, 403):
            raise GitLabAuthError(
                f"GitLab authentication failed (HTTP {response.status_code}) "
                f"for group '{group_path}'",
                details={"group_path": group_path, "status_code": response.status_code},
            )
        if response.status_code == 404:
            raise GitLabNotFoundError(
                f"GitLab group not found: '{group_path}'",
                details={"group_path": group_path},
            )
        if response.status_code >= 400:
            raise GitLabError(
                f"GitLab API error (HTTP {response.status_code}) "
                f"for group '{group_path}'",
                details={"group_path": group_path, "status_code": response.status_code},
            )

        return response
