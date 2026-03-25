"""Zoekt lifecycle manager — process management for zoekt-index and zoekt-webserver."""

from __future__ import annotations

import asyncio
import glob
import logging
import shutil
from pathlib import Path

from code_intel_mcp.errors import BinaryNotFoundError, CodeIntelError
from code_intel_mcp.models import BinaryStatus
from code_intel_mcp.zoekt_installer import find_binary

logger = logging.getLogger(__name__)

ZOEKT_WEBSERVER_PORT = 6070
INDEX_TIMEOUT_SECONDS = 30
INSTALL_INSTRUCTIONS = (
    "Zoekt binaries not found. Run 'code-intel-mcp setup' to auto-install them,\n"
    "or install manually with:\n"
    "  go install github.com/sourcegraph/zoekt/cmd/zoekt-index@latest\n"
    "  go install github.com/sourcegraph/zoekt/cmd/zoekt-webserver@latest\n"
    "Make sure $GOPATH/bin (usually ~/go/bin) is on your PATH."
)


class ZoektLifecycle:
    """Manages the zoekt-index and zoekt-webserver processes."""

    def __init__(self, index_dir: Path) -> None:
        self.index_dir = index_dir
        self._webserver_process: asyncio.subprocess.Process | None = None

    async def verify_binaries(self) -> BinaryStatus:
        """Check that zoekt-index, zoekt-webserver, and git are on PATH.

        Returns a BinaryStatus indicating which binaries were found.
        """
        status = BinaryStatus(
            zoekt_index_found=find_binary("zoekt-index") is not None,
            zoekt_webserver_found=find_binary("zoekt-webserver") is not None,
            git_found=shutil.which("git") is not None,
        )
        return status

    async def start_webserver(self) -> None:
        """Launch zoekt-webserver as a long-lived subprocess on port 6070.

        Raises BinaryNotFoundError if zoekt-webserver is not on PATH.
        Raises CodeIntelError if the process fails to start after one retry.
        """
        webserver_path = find_binary("zoekt-webserver")
        if webserver_path is None:
            raise BinaryNotFoundError(
                "zoekt-webserver binary not found.\n" + INSTALL_INSTRUCTIONS,
                details={"binary": "zoekt-webserver"},
            )

        if self.is_webserver_running():
            logger.info("zoekt-webserver is already running")
            return

        self.index_dir.mkdir(parents=True, exist_ok=True)

        for attempt in range(2):
            try:
                self._webserver_process = await asyncio.create_subprocess_exec(
                    webserver_path,
                    "-index", str(self.index_dir),
                    "-listen", f":{ZOEKT_WEBSERVER_PORT}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                # Give the process a moment to fail fast if it can't bind the port, etc.
                try:
                    await asyncio.wait_for(self._webserver_process.wait(), timeout=0.5)
                    # If we get here, the process exited immediately — that's a failure
                    stderr = b""
                    if self._webserver_process.stderr:
                        stderr = await self._webserver_process.stderr.read()
                    raise CodeIntelError(
                        f"zoekt-webserver exited immediately (code {self._webserver_process.returncode})",
                        details={"stderr": stderr.decode(errors="replace")},
                    )
                except TimeoutError:
                    # Process is still running after 0.5s — that's the success case
                    logger.info(
                        "zoekt-webserver started on port %d (pid %d)",
                        ZOEKT_WEBSERVER_PORT,
                        self._webserver_process.pid,
                    )
                    return
            except CodeIntelError:
                if attempt == 0:
                    logger.warning("zoekt-webserver failed to start, retrying once")
                    self._webserver_process = None
                    continue
                raise
            except OSError as exc:
                if attempt == 0:
                    logger.warning("zoekt-webserver failed to start: %s, retrying once", exc)
                    self._webserver_process = None
                    continue
                raise CodeIntelError(
                    f"Failed to start zoekt-webserver after retry: {exc}",
                    details={"error": str(exc)},
                ) from exc

    async def stop_webserver(self) -> None:
        """Terminate the zoekt-webserver subprocess if it is running."""
        if self._webserver_process is None:
            return

        if self._webserver_process.returncode is not None:
            # Already exited
            logger.info("zoekt-webserver already exited (code %d)", self._webserver_process.returncode)
            self._webserver_process = None
            return

        logger.info("Stopping zoekt-webserver (pid %d)", self._webserver_process.pid)
        self._webserver_process.terminate()
        try:
            await asyncio.wait_for(self._webserver_process.wait(), timeout=5.0)
        except TimeoutError:
            logger.warning("zoekt-webserver did not terminate in 5s, killing")
            self._webserver_process.kill()
            await self._webserver_process.wait()

        self._webserver_process = None

    async def index_repo(self, repo_path: Path) -> None:
        """Invoke zoekt-index for a single repository.

        Raises BinaryNotFoundError if zoekt-index is not on PATH.
        Raises CodeIntelError if indexing fails after one retry.
        """
        index_path = find_binary("zoekt-index")
        if index_path is None:
            raise BinaryNotFoundError(
                "zoekt-index binary not found.\n" + INSTALL_INSTRUCTIONS,
                details={"binary": "zoekt-index"},
            )

        self.index_dir.mkdir(parents=True, exist_ok=True)

        for attempt in range(2):
            try:
                process = await asyncio.create_subprocess_exec(
                    index_path,
                    "-index", str(self.index_dir),
                    str(repo_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=INDEX_TIMEOUT_SECONDS
                )

                if process.returncode != 0:
                    raise CodeIntelError(
                        f"zoekt-index failed for {repo_path} (code {process.returncode})",
                        details={
                            "repo_path": str(repo_path),
                            "returncode": process.returncode,
                            "stderr": stderr.decode(errors="replace"),
                        },
                    )

                logger.info("Indexed repo at %s", repo_path)
                return

            except TimeoutError:
                if attempt == 0:
                    logger.warning("zoekt-index timed out for %s, retrying once", repo_path)
                    continue
                raise CodeIntelError(
                    f"zoekt-index timed out for {repo_path} after {INDEX_TIMEOUT_SECONDS}s",
                    details={"repo_path": str(repo_path)},
                ) from None
            except CodeIntelError:
                if attempt == 0:
                    logger.warning("zoekt-index failed for %s, retrying once", repo_path)
                    continue
                raise
            except OSError as exc:
                if attempt == 0:
                    logger.warning("zoekt-index failed for %s: %s, retrying once", repo_path, exc)
                    continue
                raise CodeIntelError(
                    f"Failed to run zoekt-index for {repo_path}: {exc}",
                    details={"repo_path": str(repo_path), "error": str(exc)},
                ) from exc

    async def remove_index(self, repo_name: str) -> None:
        """Delete Zoekt index files for a given repository.

        Index shard files follow the pattern: <repo_name>*.zoekt
        """
        pattern = str(self.index_dir / f"{repo_name}*.zoekt")
        removed = 0
        for index_file in glob.glob(pattern):
            path = Path(index_file)
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Failed to remove index file %s: %s", index_file, exc)

        if removed:
            logger.info("Removed %d index file(s) for repo %s", removed, repo_name)
        else:
            logger.debug("No index files found for repo %s", repo_name)

    def is_webserver_running(self) -> bool:
        """Check if the zoekt-webserver subprocess is alive."""
        if self._webserver_process is None:
            return False
        return self._webserver_process.returncode is None
