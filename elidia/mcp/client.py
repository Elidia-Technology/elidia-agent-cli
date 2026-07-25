import asyncio
import json
import logging
import os
from typing import Any

from elidia.mcp.types import MCPCallResult, MCPResource, MCPServerConfig, MCPTool

logger = logging.getLogger(__name__)


class MCPClient:
    """Client for a single MCP server via stdio JSON-RPC 2.0."""

    def __init__(self, config: MCPServerConfig):
        logger.debug(f"Entered into MCPClient.__init__: server={config.name}")
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._request_id: int = 0
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._tools: list[MCPTool] = []
        self._resources: list[MCPResource] = []
        self._read_task: asyncio.Task[None] | None = None
        self._connected: bool = False

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tools(self) -> list[MCPTool]:
        return self._tools

    @property
    def resources(self) -> list[MCPResource]:
        return self._resources

    async def connect(self) -> None:
        logger.debug(f"Entered into MCPClient.connect: server={self.name}")
        env = dict(os.environ)
        env.update(self._config.env)

        self._process = await asyncio.create_subprocess_exec(
            self._config.command,
            *self._config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self._config.cwd,
        )

        self._read_task = asyncio.create_task(self._read_loop())

        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}, "resources": {}},
            "clientInfo": {"name": "elidia-cli", "version": "0.1.0"},
        })

        await self._send_notification("notifications/initialized", {})
        self._connected = True

        await self._discover_tools()
        await self._discover_resources()

    async def disconnect(self) -> None:
        logger.debug(f"Entered into MCPClient.disconnect: server={self.name}")
        self._connected = False
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._process and self._process.stdin:
            self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
            self._process = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPCallResult:
        logger.debug(f"Entered into call_tool: server={self.name}, tool={tool_name}")
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return MCPCallResult(
            content=result.get("content", []),
            is_error=result.get("isError", False),
        )

    async def _discover_tools(self) -> None:
        logger.debug(f"Entered into _discover_tools: server={self.name}")
        result = await self._send_request("tools/list", {})
        self._tools = []
        for t in result.get("tools", []):
            self._tools.append(MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_name=self.name,
            ))
        logger.debug(f"Discovered {len(self._tools)} tools from {self.name}")

    async def _discover_resources(self) -> None:
        logger.debug(f"Entered into _discover_resources: server={self.name}")
        try:
            result = await self._send_request("resources/list", {})
            self._resources = []
            for r in result.get("resources", []):
                self._resources.append(MCPResource(
                    uri=r["uri"],
                    name=r.get("name", ""),
                    description=r.get("description", ""),
                    mime_type=r.get("mimeType", ""),
                ))
        except Exception:
            self._resources = []

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        logger.debug(f"Entered into _send_request: method={method}")
        if not self._process or not self._process.stdin:
            raise RuntimeError(f"MCP server '{self.name}' is not running")

        self._request_id += 1
        req_id = self._request_id

        message = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._pending[req_id] = future

        line = json.dumps(message) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
        except TimeoutError as err:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP request {method} to {self.name} timed out after 30s") from err

        if "error" in result:
            err = result["error"]
            raise RuntimeError(f"MCP error from {self.name}: {err.get('message', err)}")

        return result.get("result", {})

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        logger.debug(f"Entered into _send_notification: method={method}")
        if not self._process or not self._process.stdin:
            return
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        line = json.dumps(message) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        logger.debug(f"Entered into _read_loop: server={self.name}")
        try:
            while self._process and self._process.stdout:
                line_bytes = await self._process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode().strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"Non-JSON from {self.name}: {line[:100]}")
                    continue

                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if not future.done():
                        future.set_result(msg)
                elif "method" in msg and "id" not in msg:
                    logger.debug(f"MCP notification from {self.name}: {msg.get('method')}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"MCP read loop error for {self.name}: {e}")
        finally:
            self._connected = False
