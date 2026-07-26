"""IPC between the elidia CLI (any invocation) and a running background daemon.

Unix domain socket, newline-delimited single-line JSON request/response —
the daemon process is single-purpose and long-lived, so this doesn't need
anything heavier than that. Chosen over TCP to avoid port conflicts and
scope access to the local filesystem (socket file permissions).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IPC_TIMEOUT_SECONDS = 5.0


class DaemonIPCServer:
    """Runs inside the daemon process. Answers status/stop/list_tasks requests."""

    def __init__(self, socket_path: Path, handler: Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]):
        logger.debug(f"Entered into DaemonIPCServer.__init__: socket_path={socket_path}")
        self._socket_path = socket_path
        self._handler = handler
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        logger.debug(f"Entered into DaemonIPCServer.start: socket_path={self._socket_path}")
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(self._handle_client, path=str(self._socket_path))
        self._socket_path.chmod(0o600)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=IPC_TIMEOUT_SECONDS)
            if not line:
                return
            try:
                request = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                response = {"ok": False, "error": "invalid JSON request"}
            else:
                try:
                    response = await self._handler(request)
                except Exception as e:
                    logger.warning(f"IPC handler error: {e}")
                    response = {"ok": False, "error": str(e)}
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        except (TimeoutError, ConnectionError) as e:
            logger.debug(f"IPC client error: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def stop(self) -> None:
        logger.debug("Entered into DaemonIPCServer.stop")
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._socket_path.exists():
            self._socket_path.unlink()


async def send_request(socket_path: Path, request: dict[str, Any], timeout: float = IPC_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Client side — used by CLI commands (any process) to talk to a running daemon.

    Raises ConnectionRefusedError / FileNotFoundError / TimeoutError if no
    daemon is listening — callers should treat that as "daemon not running".
    """
    logger.debug(f"Entered into send_request: socket_path={socket_path}, cmd={request.get('cmd')}")
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(path=str(socket_path)), timeout=timeout,
    )
    try:
        writer.write((json.dumps(request) + "\n").encode("utf-8"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not line:
            raise ConnectionError("Daemon closed the connection without responding")
        return json.loads(line.decode("utf-8"))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
