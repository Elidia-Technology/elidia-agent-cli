"""Process lifecycle for the background daemon — spawn/detach, liveness
check, status query, and stop, driven from any `elidia daemon ...`
invocation (a separate process from whichever one ran `daemon start`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from typing import Any

from elidia.daemon.ipc import send_request
from elidia.daemon.worker import PID_FILE, SOCKET_FILE

logger = logging.getLogger(__name__)

START_WAIT_SECONDS = 5.0
STOP_GRACE_SECONDS = 8.0


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, owned by someone else — still "alive" for our purposes.
        return True


def is_daemon_running() -> tuple[bool, int | None]:
    logger.debug("Entered into is_daemon_running")
    pid = _read_pid()
    if pid is None:
        return False, None
    if not _process_alive(pid):
        # Stale PID file from a previous crash — clean it up.
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        return False, None
    return True, pid


def start_daemon() -> int:
    """Spawn the daemon worker as a detached background process. Returns its PID.

    Raises RuntimeError if a daemon is already running, or if the process
    fails to come up within START_WAIT_SECONDS.
    """
    logger.debug("Entered into start_daemon")
    running, pid = is_daemon_running()
    if running:
        raise RuntimeError(f"Daemon already running (pid {pid})")

    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform != "win32":
        kwargs["start_new_session"] = True  # detach from the controlling terminal/session

    proc = subprocess.Popen([sys.executable, "-m", "elidia.daemon.worker"], **kwargs)

    deadline = time.monotonic() + START_WAIT_SECONDS
    while time.monotonic() < deadline:
        running, written_pid = is_daemon_running()
        if running:
            return written_pid
        if proc.poll() is not None:
            raise RuntimeError(f"Daemon worker exited immediately (code {proc.returncode}) — check ~/.elidia/daemon.log")
        time.sleep(0.1)

    raise RuntimeError("Daemon did not report ready within the timeout — check ~/.elidia/daemon.log")


async def get_daemon_status() -> dict[str, Any] | None:
    """Query a running daemon's live status over IPC. Returns None if not running."""
    logger.debug("Entered into get_daemon_status")
    running, _pid = is_daemon_running()
    if not running:
        return None
    try:
        response = await send_request(SOCKET_FILE, {"cmd": "status"})
    except (ConnectionError, OSError, TimeoutError, FileNotFoundError):
        return None
    if not response.get("ok"):
        return None
    return response.get("status")


async def stop_daemon() -> bool:
    """Ask the daemon to shut down cleanly. Returns True if it was running and is now stopped."""
    logger.debug("Entered into stop_daemon")
    running, pid = is_daemon_running()
    if not running or pid is None:
        return False

    try:
        await send_request(SOCKET_FILE, {"cmd": "stop"})
    except (ConnectionError, OSError, TimeoutError, FileNotFoundError) as e:
        logger.warning(f"IPC stop failed ({e}), falling back to SIGTERM")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            if PID_FILE.exists():
                try:
                    PID_FILE.unlink()
                except OSError:
                    pass
            return True
        await asyncio.sleep(0.2)

    # Still alive after the grace period — force it.
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    return True
