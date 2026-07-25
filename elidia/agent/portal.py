import json
import logging
from typing import Any

import httpx

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


class PortalToolBridge:
    """Executes AiUtils portal tools (111 tools) via the Developer API."""

    def __init__(self, api_key: str, base_url: str = "https://developer.aiutils.io/v1") -> None:
        logger.debug(f"Entered into PortalToolBridge.__init__: base_url={base_url}")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._tools: list[dict[str, Any]] = []

    async def discover_tools(self) -> list[dict[str, Any]]:
        logger.debug("Entered into discover_tools")
        async with httpx.AsyncClient(
            timeout=30,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        ) as client:
            try:
                resp = await client.get(f"{self._base_url}/tools")
                resp.raise_for_status()
                data = resp.json()
                self._tools = data.get("tools", data.get("data", []))
                logger.info(f"Discovered {len(self._tools)} portal tools")
                return self._tools
            except Exception as e:
                logger.warning(f"Failed to discover portal tools: {e}")
                return []

    async def execute_tool(self, tool_slug: str, inputs: dict[str, Any]) -> ToolResult:
        logger.debug(f"Entered into execute_tool: tool_slug={tool_slug}")
        async with httpx.AsyncClient(
            timeout=120,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        ) as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/tools/{tool_slug}/execute",
                    json={"inputs": inputs},
                )
                if resp.status_code == 402:
                    return ToolResult(content="Insufficient balance for this tool", is_error=True)
                if resp.status_code == 404:
                    return ToolResult(content=f"Tool '{tool_slug}' not found on portal", is_error=True)
                resp.raise_for_status()

                data = resp.json()
                output = data.get("output", data.get("result", data))

                if isinstance(output, dict):
                    content = json.dumps(output, indent=2, default=str)
                elif isinstance(output, list):
                    content = json.dumps(output, indent=2, default=str)
                else:
                    content = str(output)

                return ToolResult(
                    content=content,
                    metadata={"tool_slug": tool_slug, "credits_used": data.get("credits_used", 0)},
                )
            except httpx.HTTPStatusError as e:
                return ToolResult(content=f"Portal API error {e.response.status_code}: {e}", is_error=True)
            except Exception as e:
                return ToolResult(content=f"Portal tool error: {e}", is_error=True)

    def register_portal_tools(self, registry: ToolRegistry) -> None:
        logger.debug("Entered into register_portal_tools")
        for tool in self._tools:
            slug = tool.get("slug", tool.get("name", ""))
            if not slug:
                continue

            description = tool.get("description", f"Portal tool: {slug}")
            input_schema = tool.get("input_schema", tool.get("parameters", {}))
            if not input_schema:
                input_schema = {"type": "object", "properties": {}}

            async def handler(tool_slug: str = slug, **kwargs: Any) -> ToolResult:
                return await self.execute_tool(tool_slug, kwargs)

            registry.register(ToolDefinition(
                name=f"portal__{slug}",
                description=description[:200],
                parameters=input_schema,
                handler=handler,
                category="portal",
            ))

        logger.info(f"Registered {len(self._tools)} portal tools in registry")

    def get_tool_list(self) -> list[dict[str, str]]:
        logger.debug("Entered into get_tool_list")
        return [
            {
                "slug": t.get("slug", t.get("name", "")),
                "name": t.get("name", ""),
                "description": t.get("description", "")[:100],
                "genre": t.get("genre", ""),
            }
            for t in self._tools
        ]
