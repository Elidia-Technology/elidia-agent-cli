import logging
from typing import Any

from elidia.mcp.client import MCPClient
from elidia.mcp.config import load_mcp_config
from elidia.mcp.types import MCPCallResult, MCPServerConfig, MCPTool

logger = logging.getLogger(__name__)


class MCPRegistry:
    """Manages all MCP server connections and provides unified tool access."""

    def __init__(self) -> None:
        logger.debug("Entered into MCPRegistry.__init__")
        self._clients: dict[str, MCPClient] = {}
        self._configs: dict[str, MCPServerConfig] = {}

    async def load_and_connect(self) -> None:
        logger.debug("Entered into load_and_connect")
        self._configs = load_mcp_config()
        for name, config in self._configs.items():
            if config.enabled:
                await self.connect_server(name, config)

    async def connect_server(self, name: str, config: MCPServerConfig) -> None:
        logger.debug(f"Entered into connect_server: name={name}")
        client = MCPClient(config)
        try:
            await client.connect()
            self._clients[name] = client
            logger.info(f"Connected to MCP server '{name}': {len(client.tools)} tools")
        except Exception as e:
            logger.warning(f"Failed to connect MCP server '{name}': {e}")

    async def disconnect_all(self) -> None:
        logger.debug("Entered into disconnect_all")
        for name, client in self._clients.items():
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting {name}: {e}")
        self._clients.clear()

    async def disconnect_server(self, name: str) -> None:
        logger.debug(f"Entered into disconnect_server: name={name}")
        if name in self._clients:
            await self._clients[name].disconnect()
            del self._clients[name]

    def list_all_tools(self) -> list[MCPTool]:
        logger.debug("Entered into list_all_tools")
        tools: list[MCPTool] = []
        for client in self._clients.values():
            tools.extend(client.tools)
        return tools

    def find_tool(self, tool_name: str) -> tuple[MCPClient, MCPTool] | None:
        logger.debug(f"Entered into find_tool: tool_name={tool_name}")
        for client in self._clients.values():
            for tool in client.tools:
                if tool.name == tool_name:
                    return client, tool
        for client in self._clients.values():
            for tool in client.tools:
                if f"{client.name}__{tool.name}" == tool_name:
                    return client, tool
        return None

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPCallResult:
        logger.debug(f"Entered into call_tool: tool_name={tool_name}")
        result = self.find_tool(tool_name)
        if result is None:
            return MCPCallResult(
                content=[{"type": "text", "text": f"Tool '{tool_name}' not found"}],
                is_error=True,
            )
        client, tool = result
        return await client.call_tool(tool.name, arguments)

    def get_connected_servers(self) -> dict[str, int]:
        logger.debug("Entered into get_connected_servers")
        return {
            name: len(client.tools)
            for name, client in self._clients.items()
            if client.connected
        }

    def get_tool_schemas_for_llm(self) -> list[dict[str, Any]]:
        logger.debug("Entered into get_tool_schemas_for_llm")
        schemas: list[dict[str, Any]] = []
        for tool in self.list_all_tools():
            schemas.append({
                "type": "function",
                "function": {
                    "name": f"{tool.server_name}__{tool.name}" if tool.server_name else tool.name,
                    "description": tool.description or f"Tool from {tool.server_name}",
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            })
        return schemas
