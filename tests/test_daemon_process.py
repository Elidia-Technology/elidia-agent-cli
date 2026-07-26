"""Tests for elidia.daemon.process — PID/liveness logic (fast, in-process)
plus one real end-to-end test that actually spawns the daemon as a
detached subprocess and talks to it from this separate test process over
real IPC — the exact scenario `elidia daemon start` / `elidia daemon
status` (two different shells) depends on. No mocking of the daemon
process itself: it's a real python -m elidia.daemon.worker subprocess.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


class TestPidLivenessLogic:
    def test_no_pid_file_means_not_running(self, tmp_dir: Path, monkeypatch):
        import elidia.daemon.process as process_mod
        monkeypatch.setattr(process_mod, "PID_FILE", tmp_dir / "daemon.pid")

        running, pid = process_mod.is_daemon_running()
        assert running is False
        assert pid is None

    def test_stale_pid_file_is_cleaned_up(self, tmp_dir: Path, monkeypatch):
        import elidia.daemon.process as process_mod
        pid_file = tmp_dir / "daemon.pid"
        # A PID that (almost certainly) doesn't correspond to any real process.
        pid_file.write_text("999999", encoding="utf-8")
        monkeypatch.setattr(process_mod, "PID_FILE", pid_file)

        running, pid = process_mod.is_daemon_running()
        assert running is False
        assert not pid_file.exists(), "stale PID file should be removed once detected as dead"

    def test_own_pid_counts_as_alive(self, tmp_dir: Path, monkeypatch):
        """Uses this test process's own PID as a real, genuinely-alive process
        to verify the liveness check itself (os.kill(pid, 0)) without needing
        to spawn anything."""
        import elidia.daemon.process as process_mod
        pid_file = tmp_dir / "daemon.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(process_mod, "PID_FILE", pid_file)

        running, pid = process_mod.is_daemon_running()
        assert running is True
        assert pid == os.getpid()

    def test_corrupt_pid_file_treated_as_not_running(self, tmp_dir: Path, monkeypatch):
        import elidia.daemon.process as process_mod
        pid_file = tmp_dir / "daemon.pid"
        pid_file.write_text("not-a-number", encoding="utf-8")
        monkeypatch.setattr(process_mod, "PID_FILE", pid_file)

        running, pid = process_mod.is_daemon_running()
        assert running is False


class TestRealDaemonSubprocess:
    """Genuine end-to-end: spawn python -m elidia.daemon.worker as a real
    detached subprocess, verify a separate call to is_daemon_running()/
    status can see it, then stop it and verify real process termination."""

    @pytest.fixture
    def isolated_home(self, tmp_dir: Path):
        home = tmp_dir / "elidia_home"
        home.mkdir()
        return home

    def test_full_lifecycle(self, isolated_home: Path):
        env = {**os.environ, "ELIDIA_HOME": str(isolated_home)}

        proc = subprocess.Popen(
            [sys.executable, "-m", "elidia.daemon.worker"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            pid_file = isolated_home / "daemon.pid"
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not pid_file.exists():
                time.sleep(0.1)

            assert pid_file.exists(), (
                f"daemon never wrote its PID file; stderr={proc.stderr.read().decode() if proc.poll() is not None else '(still running)'}"
            )
            written_pid = int(pid_file.read_text(encoding="utf-8").strip())
            assert written_pid == proc.pid

            # Real liveness check against the real subprocess.
            os.kill(written_pid, 0)  # raises if not alive — the point of the assertion

            # Real IPC status query, from this separate process, against the
            # real running daemon subprocess.
            import asyncio

            # Recompute the socket path the same way the child does, since it
            # depends on ELIDIA_HOME which this test overrode via env, not
            # via the already-imported module constant in this process.
            import hashlib
            import tempfile

            from elidia.daemon.ipc import send_request
            home_hash = hashlib.sha256(str(isolated_home.resolve()).encode()).hexdigest()[:12]
            socket_path = Path(tempfile.gettempdir()) / f"elidia-daemon-{home_hash}.sock"

            status_response = None
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    status_response = asyncio.run(send_request(socket_path, {"cmd": "status"}, timeout=2.0))
                    break
                except (ConnectionError, OSError, FileNotFoundError):
                    time.sleep(0.2)

            assert status_response is not None, "never got a response from the real daemon subprocess"
            assert status_response["ok"] is True
            assert status_response["status"]["running"] is True

            # Real stop over IPC.
            stop_response = asyncio.run(send_request(socket_path, {"cmd": "stop"}, timeout=2.0))
            assert stop_response == {"ok": True, "stopping": True}

            proc.wait(timeout=5.0)
            assert proc.returncode == 0
            assert not pid_file.exists(), "PID file should be cleaned up on shutdown"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)
