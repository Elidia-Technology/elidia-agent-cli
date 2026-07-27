"""IPC between the elidia CLI (any invocation) and a running background daemon.

Unix domain socket, newline-delimited JSON — the daemon process is
single-purpose and long-lived, so this doesn't need anything heavier than
that. Chosen over TCP to avoid port conflicts and scope access to the
local filesystem (socket file permissions). Chosen over gRPC (which an
earlier draft plan for Elidia Agent Desktop sketched) because this already
exists, is already tested, and Desktop can reuse it directly instead of
introducing a second protocol to keep in sync — see
18_ELIDIA_DESKTOP_MASTER_PLAN.md §3.1 in the AiUtils.io repo.

Two request shapes, both newline-delimited JSON on the same socket:

  - Simple commands (status/stop/ping/list_tools/list_sessions/get_balance):
    exactly one request line in, exactly one response line out, connection
    closes. Unchanged from the original status/stop-only protocol.

  - Streaming commands (currently just "chat"): one request line in, MULTIPLE
    response lines out — one per AgentEvent (content/tool_call/tool_result/
    thinking), a final {"event": "done", ...} line, then the connection
    closes. This is what lets a client (elidia-agent-desktop, or any future
    client) render a chat turn incrementally instead of waiting for the
    whole thing to finish.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IPC_TIMEOUT_SECONDS = 5.0
# Streaming commands can legitimately run much longer than a simple status
# check (a real model call + tool round trips) — a separate, longer timeout
# for the whole exchange, not per-line.
STREAM_TIMEOUT_SECONDS = 120.0

# Commands whose handler yields multiple response lines instead of
# returning exactly one. Anything not in this set uses the original
# single-response path unchanged.
STREAMING_COMMANDS = {"chat", "research_start"}


class DaemonIPCServer:
    """Runs inside the daemon process. Answers status/stop/list_tasks/chat requests."""

    def __init__(
        self,
        socket_path: Path,
        handler: Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]],
        stream_handler: Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]] | None = None,
    ):
        logger.debug(f"Entered into DaemonIPCServer.__init__: socket_path={socket_path}")
        self._socket_path = socket_path
        self._handler = handler
        self._stream_handler = stream_handler
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
                await self._write_line(writer, {"ok": False, "error": "invalid JSON request"})
                return

            cmd = request.get("cmd", "")
            if cmd in STREAMING_COMMANDS and self._stream_handler is not None:
                await self._handle_streaming(writer, request)
            else:
                try:
                    response = await self._handler(request)
                except Exception as e:
                    logger.warning(f"IPC handler error: {e}")
                    response = {"ok": False, "error": str(e)}
                await self._write_line(writer, response)
        except (TimeoutError, ConnectionError) as e:
            logger.debug(f"IPC client error: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_streaming(self, writer: asyncio.StreamWriter, request: dict[str, Any]) -> None:
        logger.debug(f"Entered into _handle_streaming: cmd={request.get('cmd')}")
        try:
            async for event in self._stream_handler(request):
                await self._write_line(writer, event)
        except Exception as e:
            logger.warning(f"IPC stream handler error: {e}")
            await self._write_line(writer, {"event": "error", "data": str(e)})
        await self._write_line(writer, {"event": "done"})

    async def _write_line(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write((json.dumps(payload, default=str) + "\n").encode("utf-8"))
        await writer.drain()

    async def stop(self) -> None:
        logger.debug("Entered into DaemonIPCServer.stop")
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._socket_path.exists():
            self._socket_path.unlink()


async def send_request(socket_path: Path, request: dict[str, Any], timeout: float = IPC_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Client side — used by CLI commands (any process) to talk to a running daemon
    for simple, single-response commands (status/stop/ping/list_tools/...).

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


async def stream_request(
    socket_path: Path, request: dict[str, Any], timeout: float = STREAM_TIMEOUT_SECONDS,
) -> AsyncIterator[dict[str, Any]]:
    """Client side for streaming commands (currently "chat") — yields one
    dict per response line as the daemon sends it, until the {"event":
    "done"} line, then closes. `timeout` bounds the whole exchange (a real
    chat turn can involve multiple model calls + tool round trips), not
    each individual line.
    """
    logger.debug(f"Entered into stream_request: socket_path={socket_path}, cmd={request.get('cmd')}")
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(path=str(socket_path)), timeout=IPC_TIMEOUT_SECONDS,
    )
    try:
        writer.write((json.dumps(request) + "\n").encode("utf-8"))
        await writer.drain()

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("stream_request exceeded overall timeout")
            line = await asyncio.wait_for(reader.readline(), timeout=remaining)
            if not line:
                return
            event = json.loads(line.decode("utf-8"))
            yield event
            if event.get("event") == "done":
                return
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
