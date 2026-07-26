"""Background daemon worker — the process actually started by `elidia daemon start`.

Runs detached from the shell that launched it. Loads declared tasks from
~/.elidia/daemon.toml, starts them via DaemonManager, and answers IPC
requests (status/stop/ping/chat/list_sessions/list_tools) from any later
`elidia daemon ...` invocation (or from Elidia Agent Desktop) over a Unix
domain socket.

Two IPC request shapes, same socket:
  - Simple commands (status/stop/ping/list_tools/list_sessions/get_balance):
    exactly one request line in, exactly one response line out.
  - Streaming commands (chat): one request line in, MULTIPLE response lines
    out — one per AgentEvent (content/tool_call/tool_result/thinking), a
    final {"event": "done"} line, then the connection closes. The daemon
    daemon/ipc.py::DaemonIPCServer handles the socket-level streaming
    plumbing automatically for any command listed in STREAMING_COMMANDS.
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
        # Lazy-initialised by the first "chat" IPC call — AgentLoop + all
        # 36 built-in tools + permissions (the shared core Desktop wraps,
        # not the REPL's full extras like portal-bridge / memory / adaptive
        # routing, which are Phase-1 scope per the master plan §7). Stays
        # None until a real chat request arrives so the daemon's startup
        # path — status checks, watchers, schedules — never pays the cost
        # of loading the tool registry or embedding client.
        self._agent_loop: Any | None = None
        self._chat_lock = asyncio.Lock()  # serialise concurrent chat requests


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
    if cmd == "list_tools":
        return await _handle_list_tools(state)
    if cmd == "list_sessions":
        return await _handle_list_sessions(state)
    return {"ok": False, "error": f"unknown command: {cmd}"}


async def _handle_list_tools(state: WorkerState) -> dict[str, Any]:
    """Return all 36+ built-in tools registered in the default registry,
    exactly as the REPL sees them — same create_default_registry() call,
    same ToolDefinition schemas."""
    logger.debug("Entered into _handle_list_tools")
    from elidia.tools import create_default_registry

    registry = create_default_registry()
    tools = []
    for t in registry.list_tools():
        tools.append({
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters or {"type": "object", "properties": {}},
            "category": getattr(t, "category", "builtin"),
        })
    return {"ok": True, "tools": tools, "count": len(tools)}


async def _handle_list_sessions(state: WorkerState) -> dict[str, Any]:
    """Return recent sessions from the local elidia.db — same SQLite the REPL
    already writes to, same SessionManager.list_sessions() call."""
    logger.debug("Entered into _handle_list_sessions")
    from elidia.db.database import Database
    from elidia.session.manager import SessionManager

    db = Database()
    try:
        db.connect()
        sm = SessionManager(db)
        sessions = sm.list_sessions(limit=20)
        return {"ok": True, "sessions": sessions}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


async def _build_chat_stream_handler(state: WorkerState):
    """Lazily construct a streaming handler for the 'chat' IPC command.

    This is the core of Phase 0 — the same AgentLoop + 36 tools the REPL
    already runs, exposed over the daemon socket for any client (CLI tool
    or Elidia Agent Desktop) to consume.  The handler yields one dict per
    AgentEvent (content/tool_call/tool_result/thinking), and the caller
    (DaemonIPCServer._handle_streaming) writes each dict as a newline-
    delimited JSON line on the socket.

    Scoped to the core loop + built-in tools + permissions only — portal-
    bridge, memory store, persona engine, and adaptive routing are Phase-1
    scope for the Desktop master plan (§7) and are not wired here yet.
    """
    logger.debug("Entered into _build_chat_stream_handler")

    async def handler(request: dict[str, Any]):
        await _ensure_agent_loop(state)

        messages = request.get("messages", [])
        mode = request.get("mode", "chat")
        forced_model = request.get("model")
        session_id = request.get("session_id", "")
        image_urls = request.get("image_urls")

        from elidia.api.client import ChatMessage

        api_messages: list[ChatMessage] = []
        for m in messages:
            content = m.get("content", "")
            if image_urls and m.get("role") == "user":
                content_blocks: list[dict[str, Any]] = [{"type": "text", "text": content}]
                for url in image_urls:
                    content_blocks.append({"type": "image_url", "image_url": {"url": url}})
                api_messages.append(ChatMessage(role="user", content=content_blocks))
            else:
                api_messages.append(ChatMessage(role=m.get("role", "user"), content=content))

        async for event in state._agent_loop.run(
            messages=api_messages, mode=mode, forced_model=forced_model, session_id=session_id,
        ):
            yield {"event": event.kind, "data": event.data}

    return handler


async def _ensure_agent_loop(state: WorkerState) -> None:
    """Construct AgentLoop once, on first chat request.  Subsequent calls
    block on _chat_lock so the daemon never runs two concurrent agent loops
    against the same tool registry."""
    if state._agent_loop is not None:
        return

    async with state._chat_lock:
        if state._agent_loop is not None:
            return

        logger.debug("Entered into _ensure_agent_loop: constructing AgentLoop on first chat request")

        from elidia.api.client import AiUtilsClient
        from elidia.auth.keychain import get_api_key
        from elidia.config.settings import load_config
        from elidia.models.router import ModelRouter
        from elidia.permissions.audit import AuditLogger
        from elidia.permissions.manager import PermissionManager
        from elidia.tools import create_default_registry

        config = load_config()
        api_key = get_api_key()
        if not api_key:
            raise RuntimeError(
                "No API key configured — the daemon needs one for chat. "
                "Run 'elidia auth login' first (the key is read from the same "
                "keyring/fallback-file the CLI already uses, so if 'elidia ask' "
                "works in a terminal, the daemon will work too)."
            )

        client = AiUtilsClient(
            api_key=api_key,
            base_url=config.api.base_url,
            timeout=getattr(config.api, "timeout_seconds", 60),
        )
        tools = create_default_registry()
        router = ModelRouter()
        audit = AuditLogger()
        audit.open()
        permissions = PermissionManager(
            config=config.permissions,
            audit=audit,
            # prompt_fn is None — in the daemon, permissions that would need a
            # terminal prompt fail closed instead of blocking forever waiting
            # for a prompt nobody will answer. Once Phase 1 wires the
            # permission_request/permission_response IPC round-trip, prompt_fn
            # will become an async callback that blocks on the IPC response
            # from the Desktop UI. Until then EVERY_TIME-tier actions and
            # SESSION-tier first-approvals that aren't covered by auto-approve
            # config will be denied — this is the correct fail-closed default.
            prompt_fn=None,
        )

        from elidia.agent.loop import AgentLoop
        state._agent_loop = AgentLoop(
            client=client,
            tool_registry=tools,
            model_router=router,
            permission_manager=permissions,
            audit=audit,
        )
        logger.info("AgentLoop initialised in daemon for chat IPC")


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

    async def _stream_handler_wrapper(request: dict[str, Any]):
        handler = await _build_chat_stream_handler(state)
        async for event in handler(request):
            yield event

    ipc_server = DaemonIPCServer(
        SOCKET_FILE,
        handler=lambda req: _handle_ipc_request(state, req),
        stream_handler=_stream_handler_wrapper,
    )
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
