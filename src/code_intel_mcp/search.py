"""Search service for Zoekt-based code search."""

from __future__ import annotations

import httpx

from code_intel_mcp.errors import SearchEngineUnavailableError
from code_intel_mcp.models import FileMatch, SearchResult


class SearchService:
    """Translates search requests into Zoekt HTTP API queries and formats results."""

    def __init__(self, zoekt_url: str = "http://localhost:6070") -> None:
        self.zoekt_url = zoekt_url.rstrip("/")

    def _build_query(
        self,
        base_query: str,
        repos: list[str] | None = None,
        language: str | None = None,
        file_pattern: str | None = None,
    ) -> str:
        """Construct a Zoekt query string with optional filters."""
        parts: list[str] = []

        if repos:
            for repo in repos:
                parts.append(f"repo:^{repo}$")

        if language:
            parts.append(f"lang:{language}")

        if file_pattern:
            parts.append(f"file:{file_pattern}")

        parts.append(base_query)
        return " ".join(parts)

    async def _execute_search(self, query: str) -> dict:
        """Execute a search query against the Zoekt HTTP API."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.zoekt_url}/search",
                    params={"q": query, "format": "json", "num": "100"},
                )
                response.raise_for_status()
                return response.json()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise SearchEngineUnavailableError(
                f"Zoekt search engine is unavailable at {self.zoekt_url}: {exc}",
                details={"zoekt_url": self.zoekt_url},
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise SearchEngineUnavailableError(
                f"Zoekt returned HTTP {exc.response.status_code}: {exc}",
                details={
                    "zoekt_url": self.zoekt_url,
                    "status_code": exc.response.status_code,
                },
            ) from exc

    def _parse_search_results(self, data: dict) -> list[SearchResult]:
        """Parse Zoekt JSON response into SearchResult objects."""
        results: list[SearchResult] = []
        # Zoekt web UI format uses "result" (lowercase)
        result_obj = data.get("result", data.get("Result", {}))
        file_matches = result_obj.get("FileMatches") or []

        for fm in file_matches:
            repo_name = fm.get("Repo", fm.get("Repository", ""))
            file_path = fm.get("FileName", "")

            # Handle Matches format (web UI)
            for m in fm.get("Matches") or []:
                line_number = m.get("LineNum", 0)
                fragments = m.get("Fragments") or []
                content_parts = []
                for frag in fragments:
                    pre = frag.get("Pre", "")
                    match = frag.get("Match", "")
                    post = frag.get("Post", "")
                    content_parts.append(f"{pre}{match}{post}")
                content = "".join(content_parts) if content_parts else ""

                results.append(
                    SearchResult(
                        repo_name=repo_name,
                        file_path=file_path,
                        line_number=line_number,
                        content=content.strip(),
                    )
                )

            # Also handle LineMatches (API format)
            for lm in fm.get("LineMatches") or []:
                line_number = lm.get("LineNumber", 0)
                content = lm.get("Line", "")

                line_text = content if isinstance(content, str) else str(content)

                context_before: list[str] = []
                context_after: list[str] = []

                if "Before" in lm:
                    before = lm["Before"]
                    if isinstance(before, list):
                        context_before = [str(b) for b in before]

                if "After" in lm:
                    after = lm["After"]
                    if isinstance(after, list):
                        context_after = [str(a) for a in after]

                results.append(
                    SearchResult(
                        repo_name=repo_name,
                        file_path=file_path,
                        line_number=line_number,
                        content=line_text,
                        context_before=context_before,
                        context_after=context_after,
                    )
                )

            # Also handle ChunkMatches (newer Zoekt format)
            for cm in fm.get("ChunkMatches") or []:
                content_text = cm.get("Content", "")
                line_text = content_text.rstrip("\n") if isinstance(content_text, str) else str(content_text)

                content_start = cm.get("ContentStart", {})
                line_number = content_start.get("LineNumber", 0)

                results.append(
                    SearchResult(
                        repo_name=repo_name,
                        file_path=file_path,
                        line_number=line_number,
                        content=line_text,
                    )
                )

        return results

    def _parse_file_matches(self, data: dict) -> list[FileMatch]:
        """Parse Zoekt JSON response into FileMatch objects."""
        results: list[FileMatch] = []
        # Zoekt web UI format uses "result" (lowercase)
        result_obj = data.get("result", data.get("Result", {}))
        file_matches = result_obj.get("FileMatches") or []

        for fm in file_matches:
            repo_name = fm.get("Repo", fm.get("Repository", ""))
            file_path = fm.get("FileName", "")
            results.append(FileMatch(repo_name=repo_name, file_path=file_path))

        return results

    async def search_code(
        self,
        query: str,
        repos: list[str] | None = None,
        language: str | None = None,
        file_pattern: str | None = None,
    ) -> list[SearchResult]:
        """Search for code patterns across indexed repositories.

        Args:
            query: The search query string.
            repos: Optional list of repo names to restrict search to.
            language: Optional language filter (e.g., "java", "python").
            file_pattern: Optional file path pattern filter.

        Returns:
            List of SearchResult with file paths, line numbers, and context.

        Raises:
            SearchEngineUnavailableError: If Zoekt is unreachable.
        """
        zoekt_query = self._build_query(
            query, repos=repos, language=language, file_pattern=file_pattern
        )
        data = await self._execute_search(zoekt_query)
        return self._parse_search_results(data)

    async def search_files(
        self,
        pattern: str,
        repos: list[str] | None = None,
    ) -> list[FileMatch]:
        """Search for files by name pattern across indexed repositories.

        Args:
            pattern: Filename pattern to search for.
            repos: Optional list of repo names to restrict search to.

        Returns:
            List of FileMatch with repo names and file paths.

        Raises:
            SearchEngineUnavailableError: If Zoekt is unreachable.
        """
        zoekt_query = self._build_query(f"file:{pattern}", repos=repos)
        data = await self._execute_search(zoekt_query)
        return self._parse_file_matches(data)

    async def search_references(
        self,
        symbol: str,
        repos: list[str] | None = None,
    ) -> list[SearchResult]:
        r"""Search for symbol references using word-boundary matching.

        Constructs a `\b{symbol}\b` regex query to find whole-word matches,
        avoiding partial matches within larger identifiers.

        Args:
            symbol: The symbol name to search for.
            repos: Optional list of repo names to restrict search to.

        Returns:
            List of SearchResult with file paths, line numbers, and context.

        Raises:
            SearchEngineUnavailableError: If Zoekt is unreachable.
        """
        regex_query = f"\\b{symbol}\\b"
        zoekt_query = self._build_query(regex_query, repos=repos)
        data = await self._execute_search(zoekt_query)
        return self._parse_search_results(data)

    async def health_check(self) -> bool:
        """Verify that the Zoekt webserver is reachable.

        Returns:
            True if the Zoekt webserver responds successfully.

        Raises:
            SearchEngineUnavailableError: If Zoekt is unreachable.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.zoekt_url}/healthz")
                return response.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise SearchEngineUnavailableError(
                f"Zoekt search engine is unavailable at {self.zoekt_url}: {exc}",
                details={"zoekt_url": self.zoekt_url},
            ) from exc
