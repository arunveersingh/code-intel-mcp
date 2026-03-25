"""Unit tests for GitLabClient.

Tests mock httpx at the transport level so the test module itself does
not need httpx installed — the production code imports it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_intel_mcp.errors import GitLabAuthError, GitLabError, GitLabNotFoundError
from code_intel_mcp.gitlab_client import GitLabClient

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_project(n: int) -> dict:
    """Return a minimal GitLab project JSON dict."""
    return {
        "name": f"project-{n}",
        "path_with_namespace": f"group/project-{n}",
        "http_url_to_repo": f"https://gitlab.example.com/group/project-{n}.git",
        "ssh_url_to_repo": f"git@gitlab.example.com:group/project-{n}.git",
    }


def _fake_response(status_code: int, json_data=None, headers=None):
    """Build a lightweight fake response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else []
    resp.headers = headers or {}
    return resp


# ------------------------------------------------------------------
# from_env factory
# ------------------------------------------------------------------

class TestFromEnv:
    def test_both_vars_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-abc123")
        client = GitLabClient.from_env()
        assert client._base_url == "https://gitlab.example.com"
        assert client._token == "glpat-abc123"

    def test_missing_url(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("GITLAB_URL", raising=False)
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-abc123")
        with pytest.raises(GitLabAuthError, match="GITLAB_URL"):
            GitLabClient.from_env()

    def test_missing_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        with pytest.raises(GitLabAuthError, match="GITLAB_TOKEN"):
            GitLabClient.from_env()

    def test_both_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("GITLAB_URL", raising=False)
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        with pytest.raises(GitLabAuthError) as exc_info:
            GitLabClient.from_env()
        assert "GITLAB_URL" in exc_info.value.message
        assert "GITLAB_TOKEN" in exc_info.value.message

    def test_strips_trailing_slash(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com/")
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        client = GitLabClient.from_env()
        assert client._base_url == "https://gitlab.example.com"

    def test_empty_string_treated_as_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GITLAB_URL", "")
        monkeypatch.setenv("GITLAB_TOKEN", "")
        with pytest.raises(GitLabAuthError) as exc_info:
            GitLabClient.from_env()
        assert "GITLAB_URL" in exc_info.value.message
        assert "GITLAB_TOKEN" in exc_info.value.message


# ------------------------------------------------------------------
# list_group_projects
# ------------------------------------------------------------------

class TestListGroupProjects:
    @pytest.mark.asyncio
    async def test_single_page(self):
        """Single page of results (no x-next-page header)."""
        client = GitLabClient("https://gitlab.example.com", "tok")
        projects_json = [_make_project(1), _make_project(2)]
        resp = _fake_response(200, projects_json)

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("code_intel_mcp.gitlab_client.httpx.AsyncClient", return_value=mock_client_instance):
            result = await client.list_group_projects("my-group")

        assert len(result) == 2
        assert result[0].name == "project-1"
        assert result[1].path_with_namespace == "group/project-2"

        # Verify PRIVATE-TOKEN header was sent
        call_kwargs = mock_client_instance.get.call_args
        assert call_kwargs.kwargs["headers"]["PRIVATE-TOKEN"] == "tok"

    @pytest.mark.asyncio
    async def test_pagination(self):
        """Two pages of results via x-next-page header."""
        client = GitLabClient("https://gitlab.example.com", "tok")

        page1_resp = _fake_response(200, [_make_project(1)], headers={"x-next-page": "2"})
        page2_resp = _fake_response(200, [_make_project(2)], headers={})

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(side_effect=[page1_resp, page2_resp])
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("code_intel_mcp.gitlab_client.httpx.AsyncClient", return_value=mock_client_instance):
            result = await client.list_group_projects("my-group")

        assert len(result) == 2
        assert result[0].name == "project-1"
        assert result[1].name == "project-2"
        assert mock_client_instance.get.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_group(self):
        """Group with no projects returns empty list."""
        client = GitLabClient("https://gitlab.example.com", "tok")
        resp = _fake_response(200, [])

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("code_intel_mcp.gitlab_client.httpx.AsyncClient", return_value=mock_client_instance):
            result = await client.list_group_projects("empty-group")

        assert result == []

    @pytest.mark.asyncio
    async def test_auth_error_401(self):
        client = GitLabClient("https://gitlab.example.com", "bad-tok")
        resp = _fake_response(401)

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("code_intel_mcp.gitlab_client.httpx.AsyncClient", return_value=mock_client_instance):
            with pytest.raises(GitLabAuthError, match="401"):
                await client.list_group_projects("my-group")

    @pytest.mark.asyncio
    async def test_auth_error_403(self):
        client = GitLabClient("https://gitlab.example.com", "bad-tok")
        resp = _fake_response(403)

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("code_intel_mcp.gitlab_client.httpx.AsyncClient", return_value=mock_client_instance):
            with pytest.raises(GitLabAuthError, match="403"):
                await client.list_group_projects("my-group")

    @pytest.mark.asyncio
    async def test_not_found_404(self):
        client = GitLabClient("https://gitlab.example.com", "tok")
        resp = _fake_response(404)

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("code_intel_mcp.gitlab_client.httpx.AsyncClient", return_value=mock_client_instance):
            with pytest.raises(GitLabNotFoundError, match="my-group"):
                await client.list_group_projects("my-group")

    @pytest.mark.asyncio
    async def test_network_error(self):
        # Import httpx through the module that already has it loaded
        from code_intel_mcp.gitlab_client import httpx as _httpx

        client = GitLabClient("https://gitlab.example.com", "tok")

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(side_effect=_httpx.ConnectError("connection refused"))
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("code_intel_mcp.gitlab_client.httpx.AsyncClient", return_value=mock_client_instance):
            with pytest.raises(GitLabError, match="Network error"):
                await client.list_group_projects("my-group")

    @pytest.mark.asyncio
    async def test_group_path_url_encoded(self):
        """Slashes in group paths are URL-encoded."""
        client = GitLabClient("https://gitlab.example.com", "tok")
        resp = _fake_response(200, [])

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("code_intel_mcp.gitlab_client.httpx.AsyncClient", return_value=mock_client_instance):
            await client.list_group_projects("parent/child")

        called_url = mock_client_instance.get.call_args.args[0]
        assert "parent%2Fchild" in called_url

    @pytest.mark.asyncio
    async def test_server_error_500(self):
        """Generic server errors map to GitLabError."""
        client = GitLabClient("https://gitlab.example.com", "tok")
        resp = _fake_response(500)

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("code_intel_mcp.gitlab_client.httpx.AsyncClient", return_value=mock_client_instance):
            with pytest.raises(GitLabError, match="500"):
                await client.list_group_projects("my-group")
