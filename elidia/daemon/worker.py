"""Background daemon worker — the process actually started by `elidia daemon start`.

Runs detached from the shell that launched it. Loads declared tasks from
~/.elidia/daemon.toml, starts them via DaemonManager, and answers IPC
requests (status/stop/ping) from any later `elidia daemon ...` invocation
over a Unix domain socket. Writes its own PID file on startup and cleans
up both the PID file and the socket on shutdown — this is what makes
`elidia daemon start` (this process) and `elidia daemon status` (a
separate, later process) actually talk to the same running instance,
which a bare in-process DaemonManager (no persistence, no IPC) could
never do.

Invoked as: python -m elidia.daemon.worker
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from elidia.config.settings import ELIDIA_HOME
from elidia.daemon.config import load_daemon_config
from elidia.daemon.ipc import DaemonIPCServer
from elidia.daemon.manager import DaemonManager

logger = logging.getLogger(__name__)


def _short_socket_path() -> Path:
    """Unix domain sockets have a hard OS path-length limit (~104 bytes on
    macOS/BSD, 108 on Linux) — bind() raises OSError: AF_UNIX path too long
    past that, verified live 2026-07-26 with a long ELIDIA_HOME override.
    ~/.elidia is short enough by default, but nothing guarantees that (a
    user's home directory could be deeply nested, or ELIDIA_HOME could be
    overridden), so the socket specifically lives in the system temp dir
    under a short, deterministic name — hashed from the real ELIDIA_HOME so
    two different configs never collide on the same socket path.
    """
    home_hash = hashlib.sha256(str(ELIDIA_HOME.resolve()).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"elidia-daemon-{home_hash}.sock"


PID_FILE = ELIDIA_HOME / "daemon.pid"
SOCKET_FILE = _short_socket_path()
CONFIG_FILE = ELIDIA_HOME / "daemon.toml"
LOG_FILE = ELIDIA_HOME / "daemon.log"


class WorkerState:
    def __init__(self) -> None:
        self.manager = DaemonManager()
        self.shutdown_event = asyncio.Event()


async def _handle_ipc_request(state: WorkerState, request: dict[str, Any]) -> dict[str, Any]:
    cmd = request.get("cmd", "")
    logger.debug(f"Entered into _handle_ipc_request: cmd={cmd}")

    if cmd == "ping":
        return {"ok": True, "pong": True}
    if cmd == "status":
        return {"ok": True, "status": state.manager.get_status()}
    if cmd == "stop":
        state.shutdown_event.set()
        return {"ok": True, "stopping": True}
    return {"ok": False, "error": f"unknown command: {cmd}"}


async def _run() -> None:
    ELIDIA_HOME.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE)],
    )
    logger.info(f"Daemon worker starting, pid={os.getpid()}")

    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    state = WorkerState()
    config = load_daemon_config(CONFIG_FILE)

    for w in config.watchers:
        state.manager.add_watcher(w.name, w.path, patterns=w.patterns, interval=w.interval)
    for s in config.schedules:
        if s.cron:
            state.manager.add_cron_schedule(s.name, s.cron, command=s.command)
        else:
            state.manager.add_schedule(s.name, s.interval_seconds, command=s.command)
    for h in config.webhooks:
        state.manager.add_webhook(h.name, path=h.path, port=h.port)

    await state.manager.start()

    ipc_server = DaemonIPCServer(SOCKET_FILE, handler=lambda req: _handle_ipc_request(state, req))
    await ipc_server.start()
    logger.info(f"Daemon worker ready: {len(config.watchers)} watchers, {len(config.schedules)} schedules, {len(config.webhooks)} webhooks")

    try:
        await state.shutdown_event.wait()
    finally:
        logger.info("Daemon worker shutting down")
        await ipc_server.stop()
        await state.manager.stop()
        if PID_FILE.exists():
            PID_FILE.unlink()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    finally:
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main() or 0)
