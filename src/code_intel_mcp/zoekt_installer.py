"""Auto-download and install Zoekt binaries for macOS."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_BIN_DIR = Path.home() / ".code-intel-mcp" / "bin"

# Map (system, machine) → archive filename.
# Extend this dict when adding new platforms.
_PLATFORM_MAP: dict[tuple[str, str], str] = {
    ("Darwin", "arm64"): "zoekt-darwin-arm64.tar.gz",
    ("Darwin", "x86_64"): "zoekt-darwin-amd64.tar.gz",
}

# Where the archives are hosted.
# Users MUST set this env var or override via argument.
_DEFAULT_BASE_URL = os.environ.get(
    "ZOEKT_BINARY_URL",
    "",  # no default — must be configured
)

ZOEKT_BINARIES = ("zoekt-index", "zoekt-webserver")


def get_platform_key() -> tuple[str, str]:
    """Return (system, machine) for the current platform."""
    return (platform.system(), platform.machine())


def get_archive_name() -> str | None:
    """Return the archive filename for this platform, or None if unsupported."""
    return _PLATFORM_MAP.get(get_platform_key())


def is_installed() -> bool:
    """Check if both Zoekt binaries exist in the managed bin dir."""
    return all((_BIN_DIR / name).is_file() for name in ZOEKT_BINARIES)


def find_binary(name: str) -> str | None:
    """Find a Zoekt binary — check managed bin dir first, then PATH."""
    managed = _BIN_DIR / name
    if managed.is_file():
        return str(managed)
    return shutil.which(name)


def install(base_url: str | None = None) -> Path:
    """Download and install Zoekt binaries for the current platform.

    Args:
        base_url: URL prefix where the tar.gz archives are hosted.
                  Falls back to ZOEKT_BINARY_URL env var.

    Returns:
        Path to the bin directory containing the installed binaries.

    Raises:
        RuntimeError: If the platform is unsupported or download fails.
    """
    url_prefix = base_url or _DEFAULT_BASE_URL
    if not url_prefix:
        raise RuntimeError(
            "No download URL configured. Set ZOEKT_BINARY_URL environment variable "
            "or pass base_url to install()."
        )

    archive_name = get_archive_name()
    if archive_name is None:
        system, machine = get_platform_key()
        raise RuntimeError(
            f"Unsupported platform: {system}/{machine}. "
            f"Zoekt pre-built binaries are available for macOS (arm64, x86_64)."
        )

    url = f"{url_prefix.rstrip('/')}/{archive_name}"
    logger.info("Downloading Zoekt binaries from %s", url)

    _BIN_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        # Download
        with httpx.Client(follow_redirects=True, timeout=120.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            tmp_path.write_bytes(resp.content)

        # Extract
        with tarfile.open(tmp_path, "r:gz") as tar:
            # Security: only extract expected filenames
            for member in tar.getmembers():
                if member.name not in ZOEKT_BINARIES:
                    continue
                tar.extract(member, path=_BIN_DIR)

        # Make executable
        for name in ZOEKT_BINARIES:
            binary = _BIN_DIR / name
            if binary.is_file():
                binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                logger.info("Installed %s → %s", name, binary)
            else:
                raise RuntimeError(f"Expected binary '{name}' not found in archive")

    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Failed to download Zoekt binaries: HTTP {exc.response.status_code} from {url}"
        ) from exc
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Failed to connect to {url}: {exc}"
        ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return _BIN_DIR
