"""Unit tests for ZoektLifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_intel_mcp.errors import BinaryNotFoundError, CodeIntelError
from code_intel_mcp.zoekt import ZoektLifecycle

_FB = "code_intel_mcp.zoekt.find_binary"
_SW = "code_intel_mcp.zoekt.shutil.which"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def zoekt(tmp_index_dir: Path) -> ZoektLifecycle:
    return ZoektLifecycle(index_dir=tmp_index_dir)


class TestVerifyBinaries:
    def test_all_found(self, zoekt: ZoektLifecycle) -> None:
        with patch(_FB, return_value="/usr/bin/fake"), \
             patch(_SW, return_value="/usr/bin/git"):
            status = _run(zoekt.verify_binaries())
        assert status.zoekt_index_found is True
        assert status.zoekt_webserver_found is True
        assert status.git_found is True

    def test_none_found(self, zoekt: ZoektLifecycle) -> None:
        with patch(_FB, return_value=None), \
             patch(_SW, return_value=None):
            status = _run(zoekt.verify_binaries())
        assert status.zoekt_index_found is False
        assert status.zoekt_webserver_found is False
        assert status.git_found is False

    def test_partial_found(self, zoekt: ZoektLifecycle) -> None:
        with patch(_FB, return_value=None), \
             patch(_SW, return_value="/usr/bin/git"):
            status = _run(zoekt.verify_binaries())
        assert status.zoekt_index_found is False
        assert status.zoekt_webserver_found is False
        assert status.git_found is True


class TestWebserverLifecycle:
    def test_start_raises_if_binary_missing(self, zoekt: ZoektLifecycle) -> None:
        with patch(_FB, return_value=None), pytest.raises(BinaryNotFoundError, match="zoekt-webserver"):
            _run(zoekt.start_webserver())

    def test_start_success(self, zoekt: ZoektLifecycle) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None

        async def mock_wait():
            await asyncio.sleep(10)

        mock_proc.wait = mock_wait
        mock_proc.stderr = None
        mock_proc.stdout = None

        with patch(_FB, return_value="/usr/bin/zoekt-webserver"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            _run(zoekt.start_webserver())

        assert zoekt.is_webserver_running() is True

    def test_start_noop_if_already_running(self, zoekt: ZoektLifecycle) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = None
        zoekt._webserver_process = mock_proc

        with patch(_FB, return_value="/usr/bin/zoekt-webserver"):
            _run(zoekt.start_webserver())
        assert zoekt._webserver_process is mock_proc

    def test_stop_terminates_process(self, zoekt: ZoektLifecycle) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 99

        async def mock_wait():
            mock_proc.returncode = -15
            return -15

        mock_proc.wait = mock_wait
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        zoekt._webserver_process = mock_proc

        _run(zoekt.stop_webserver())
        mock_proc.terminate.assert_called_once()
        assert zoekt._webserver_process is None

    def test_stop_noop_if_not_running(self, zoekt: ZoektLifecycle) -> None:
        _run(zoekt.stop_webserver())

    def test_stop_noop_if_already_exited(self, zoekt: ZoektLifecycle) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        zoekt._webserver_process = mock_proc
        _run(zoekt.stop_webserver())
        assert zoekt._webserver_process is None

    def test_start_retries_on_immediate_exit(self, zoekt: ZoektLifecycle) -> None:
        call_count = 0

        async def make_failing_proc(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_proc = MagicMock()
            mock_proc.pid = 100 + call_count
            mock_proc.returncode = 1

            async def mock_wait():
                return 1

            mock_proc.wait = mock_wait
            stderr_mock = MagicMock()

            async def read_stderr():
                return b"bind: address already in use"

            stderr_mock.read = read_stderr
            mock_proc.stderr = stderr_mock
            mock_proc.stdout = None
            return mock_proc

        with patch(_FB, return_value="/usr/bin/zoekt-webserver"), \
             patch("asyncio.create_subprocess_exec", side_effect=make_failing_proc):
            with pytest.raises(CodeIntelError, match="exited immediately"):
                _run(zoekt.start_webserver())

        assert call_count == 2


class TestIndexRepo:
    def test_raises_if_binary_missing(self, zoekt: ZoektLifecycle) -> None:
        with patch(_FB, return_value=None), pytest.raises(BinaryNotFoundError, match="zoekt-index"):
            _run(zoekt.index_repo(Path("/tmp/some-repo")))

    def test_success(self, zoekt: ZoektLifecycle) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        async def mock_communicate():
            return (b"ok", b"")

        mock_proc.communicate = mock_communicate

        with patch(_FB, return_value="/usr/bin/zoekt-index"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            _run(zoekt.index_repo(Path("/tmp/some-repo")))

    def test_retries_on_failure_then_succeeds(self, zoekt: ZoektLifecycle) -> None:
        call_count = 0

        async def make_proc(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_proc = MagicMock()
            if call_count == 1:
                mock_proc.returncode = 1

                async def fail_communicate():
                    return (b"", b"error")

                mock_proc.communicate = fail_communicate
            else:
                mock_proc.returncode = 0

                async def ok_communicate():
                    return (b"ok", b"")

                mock_proc.communicate = ok_communicate
            return mock_proc

        with patch(_FB, return_value="/usr/bin/zoekt-index"), \
             patch("asyncio.create_subprocess_exec", side_effect=make_proc):
            _run(zoekt.index_repo(Path("/tmp/some-repo")))

        assert call_count == 2

    def test_raises_after_retry_exhausted(self, zoekt: ZoektLifecycle) -> None:
        async def make_failing_proc(*args, **kwargs):
            mock_proc = MagicMock()
            mock_proc.returncode = 1

            async def fail_communicate():
                return (b"", b"fatal error")

            mock_proc.communicate = fail_communicate
            return mock_proc

        with patch(_FB, return_value="/usr/bin/zoekt-index"), \
             patch("asyncio.create_subprocess_exec", side_effect=make_failing_proc):
            with pytest.raises(CodeIntelError, match="zoekt-index failed"):
                _run(zoekt.index_repo(Path("/tmp/some-repo")))


class TestRemoveIndex:
    def test_removes_matching_files(self, zoekt: ZoektLifecycle) -> None:
        (zoekt.index_dir / "my-repo_v1.00000.zoekt").write_text("shard1")
        (zoekt.index_dir / "my-repo_v1.00001.zoekt").write_text("shard2")
        (zoekt.index_dir / "other-repo_v1.00000.zoekt").write_text("other")
        _run(zoekt.remove_index("my-repo"))
        remaining = list(zoekt.index_dir.iterdir())
        assert len(remaining) == 1
        assert remaining[0].name == "other-repo_v1.00000.zoekt"

    def test_noop_when_no_files(self, zoekt: ZoektLifecycle) -> None:
        _run(zoekt.remove_index("nonexistent-repo"))


class TestIsWebserverRunning:
    def test_false_when_no_process(self, zoekt: ZoektLifecycle) -> None:
        assert zoekt.is_webserver_running() is False

    def test_true_when_process_alive(self, zoekt: ZoektLifecycle) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = None
        zoekt._webserver_process = mock_proc
        assert zoekt.is_webserver_running() is True

    def test_false_when_process_exited(self, zoekt: ZoektLifecycle) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        zoekt._webserver_process = mock_proc
        assert zoekt.is_webserver_running() is False
