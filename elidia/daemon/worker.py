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
import json
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
    if cmd == "rag_search":
        return await _handle_rag_search(request)
    if cmd == "rag_list_sources":
        return await _handle_rag_list_sources()
    if cmd == "get_daemon_config":
        return _handle_get_daemon_config()
    if cmd == "get_audit_log":
        return _handle_get_audit_log(request)
    if cmd == "workflow_run":
        return await _handle_workflow_run(state, request)
    if cmd == "get_balance":
        return await _handle_get_balance()
    if cmd == "list_mcp_servers":
        return await _handle_list_mcp_servers()
    if cmd == "list_personas":
        return _handle_list_personas()
    if cmd == "list_models":
        return _handle_list_models()
    if cmd == "search_memory":
        return _handle_search_memory(request)
    if cmd == "forget_memory":
        return _handle_forget_memory(request)
    if cmd == "get_trust_stats":
        return _handle_get_trust_stats(state)
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


async def _handle_rag_search(request: dict[str, Any]) -> dict[str, Any]:
    """Search ingested RAG content — reuses elidia/tools/rag.py directly,
    not a separate implementation."""
    logger.debug("Entered into _handle_rag_search")
    from elidia.tools.rag import _rag_search

    query = request.get("query", "")
    limit = request.get("limit", 5)
    if not query:
        return {"ok": False, "error": "missing required field: query"}
    result = await _rag_search(query, limit=limit)
    return {"ok": not result.is_error, "content": result.content, "is_error": result.is_error}


async def _handle_rag_list_sources() -> dict[str, Any]:
    logger.debug("Entered into _handle_rag_list_sources")
    from elidia.tools.rag import _rag_list_sources

    result = await _rag_list_sources()
    return {"ok": not result.is_error, "content": result.content, "is_error": result.is_error}


def _handle_get_daemon_config() -> dict[str, Any]:
    """Return the current daemon.toml contents as a structured dict,
    not raw TOML — the Desktop dashboard form reads/writes this shape."""
    logger.debug("Entered into _handle_get_daemon_config")
    config = load_daemon_config(CONFIG_FILE)
    return {
        "ok": True,
        "config": {
            "watchers": [
                {"name": w.name, "path": w.path, "patterns": w.patterns, "interval": w.interval}
                for w in config.watchers
            ],
            "schedules": [
                {"name": s.name, "cron": s.cron, "command": s.command, "interval_seconds": s.interval_seconds}
                for s in config.schedules
            ],
            "webhooks": [
                {"name": h.name, "path": h.path, "port": h.port}
                for h in config.webhooks
            ],
        },
    }


def _handle_get_audit_log(request: dict[str, Any]) -> dict[str, Any]:
    """Return the last N lines of the audit log as parsed JSON objects."""
    logger.debug("Entered into _handle_get_audit_log")
    limit = request.get("limit", 50)
    audit_path = ELIDIA_HOME / "audit.log"
    if not audit_path.exists():
        return {"ok": True, "entries": []}
    lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
    recent = lines[-limit:]
    entries = []
    for line in recent:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return {"ok": True, "entries": entries}


async def _handle_workflow_run(state: WorkerState, request: dict[str, Any]) -> dict[str, Any]:
    """Execute a workflow from a YAML string. Returns a summary of results,
    not a streaming response — workflows can have many steps and the caller
    typically wants the final summary, not per-step progress."""
    logger.debug("Entered into _handle_workflow_run")
    yaml_text = request.get("yaml", "")
    if not yaml_text:
        return {"ok": False, "error": "missing required field: yaml"}

    from elidia.workflow.engine import WorkflowExecutor, parse_workflow
    from elidia.api.client import AiUtilsClient
    from elidia.auth.keychain import get_api_key
    from elidia.config.settings import load_config
    from elidia.tools.base import ToolRegistry
    from elidia.tools import create_default_registry

    try:
        wf = parse_workflow(yaml_text)
    except Exception as e:
        return {"ok": False, "error": f"Failed to parse workflow: {e}"}

    # Build a client only if there's an llm step (same logic as the CLI fix, AIUT-2141)
    from elidia.workflow.engine import workflow_requires_llm
    client = None
    if workflow_requires_llm(wf):
        api_key = get_api_key()
        if not api_key:
            return {"ok": False, "error": "Workflow has llm steps but no API key configured"}
        config = load_config()
        client = AiUtilsClient(api_key=api_key, base_url=config.api.base_url)

    # Wire up a tool executor that uses the shared registry
    registry: ToolRegistry = create_default_registry()

    async def tool_execute_fn(tool_name: str, arguments: dict[str, Any]) -> str:
        result = await registry.call(tool_name, arguments)
        return result.content

    executor = WorkflowExecutor(client=client, tool_execute_fn=tool_execute_fn)

    results = []
    completed = 0
    failed = 0
    try:
        async for event in executor.run(wf):
            if event.kind == "step_done":
                results.append(event.data)
                if event.data.get("status") == "completed":
                    completed += 1
                elif event.data.get("status") == "failed":
                    failed += 1
    finally:
        if client is not None:
            await client.close()

    return {
        "ok": True,
        "workflow_name": wf.name,
        "completed": completed,
        "failed": failed,
        "steps": results,
    }


# ---- memory handlers ----

def _handle_search_memory(request: dict[str, Any]) -> dict[str, Any]:
    logger.debug("Entered into _handle_search_memory")
    from elidia.memory.store import MemoryStore
    query = request.get("query", "")
    limit = request.get("limit", 10)
    store = MemoryStore()
    try:
        store.open()
        results = store.search_text(query, limit=limit) if query else store.list_memories(limit=limit)
        return {"ok": True, "entries": [{"key": m.key, "content": m.content[:200], "tier": str(m.tier)} for m in results]}
    finally:
        store.close()


def _handle_forget_memory(request: dict[str, Any]) -> dict[str, Any]:
    logger.debug("Entered into _handle_forget_memory")
    from elidia.memory.store import MemoryStore
    key = request.get("key", "")
    if not key:
        return {"ok": False, "error": "missing required field: key"}
    store = MemoryStore()
    try:
        store.open()
        n = store.delete_by_key(key)
        return {"ok": True, "deleted": n}
    finally:
        store.close()


# ---- MCP handlers ----

async def _handle_list_mcp_servers() -> dict[str, Any]:
    logger.debug("Entered into _handle_list_mcp_servers")
    from elidia.mcp.registry import MCPRegistry
    registry = MCPRegistry()
    try:
        await registry.load_and_connect()
        servers = registry.get_connected_servers()
        await registry.disconnect_all()
        return {"ok": True, "servers": {str(k): v for k, v in servers.items()}}
    except Exception as e:
        return {"ok": True, "servers": {}, "error": str(e)}


# ---- persona + model handlers ----

def _handle_list_personas() -> dict[str, Any]:
    logger.debug("Entered into _handle_list_personas")
    from elidia.agent.personas import PersonaEngine
    engine = PersonaEngine()
    personas = engine.list_personas()
    return {"ok": True, "personas": personas, "active": getattr(engine, "active_slug", "")}


def _handle_list_models() -> dict[str, Any]:
    logger.debug("Entered into _handle_list_models")
    from elidia.models.router import ModelRouter
    router = ModelRouter()
    # get_model_for_type returns the best model per task category —
    # this mirrors what the agent actually routes to (no separate catalog).
    task_types = ["chat", "code", "reasoning", "creative", "vision", "cheap"]
    models: dict[str, str] = {}
    for t in task_types:
        try:
            models[t] = router.get_model_for_type(t)
        except Exception:
            pass
    return {"ok": True, "models": models}


# ---- balance + budget ----

async def _handle_get_balance() -> dict[str, Any]:
    logger.debug("Entered into _handle_get_balance")
    from elidia.auth.keychain import get_api_key
    from elidia.config.settings import load_config
    from elidia.api.client import AiUtilsClient

    api_key = get_api_key()
    if not api_key:
        return {"ok": False, "error": "No API key configured"}
    config = load_config()
    client = AiUtilsClient(api_key=api_key, base_url=config.api.base_url)
    try:
        balance = await client.get_balance()
        return {"ok": True, "balance": balance}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        await client.close()


# ---- trust stats ----

def _handle_get_trust_stats(state: WorkerState) -> dict[str, Any]:
    logger.debug("Entered into _handle_get_trust_stats")
    if state._agent_loop is None or state._agent_loop._permissions is None:
        return {"ok": True, "promotions": [], "note": "No chat sessions yet — trust data builds up as you use the agent."}
    perm = state._agent_loop._permissions
    trust = getattr(perm, "_trust", None)
    if trust is None:
        return {"ok": True, "promotions": [], "note": "Trust engine not initialized."}
    # TrustEngine tracks per-action approval history internally; expose the
    # actions that have been promoted (is_promoted returns True).
    promoted: list[str] = []
    for action in [
        "command_exec", "file_write_project", "file_delete", "browser_interact",
        "db_query", "email_send", "file_read_external",
    ]:
        if trust.is_promoted(action):
            promoted.append(action)
    return {"ok": True, "promoted_actions": promoted, "note": "Promoted actions skip the permission prompt."}


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
