import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    content: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: Callable[..., Awaitable[ToolResult]] | None = None
    category: str = "builtin"


class ToolRegistry:
    """Registry of all built-in tools."""

    def __init__(self) -> None:
        logger.debug("Entered into ToolRegistry.__init__")
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        logger.debug(f"Entered into register: name={tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        logger.debug(f"Entered into get: name={name}")
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        logger.debug("Entered into list_tools")
        return list(self._tools.values())

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        logger.debug(f"Entered into call: name={name}")
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(content=f"Tool '{name}' not found", is_error=True)
        if not tool.handler:
            return ToolResult(content=f"Tool '{name}' has no handler", is_error=True)
        try:
            return await tool.handler(**arguments)
        except TypeError as e:
            return ToolResult(content=f"Invalid arguments for '{name}': {e}", is_error=True)
        except Exception as e:
            return ToolResult(content=f"Tool '{name}' error: {e}", is_error=True)

    def get_schemas_for_llm(self) -> list[dict[str, Any]]:
        logger.debug("Entered into get_schemas_for_llm")
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                },
            })
        return schemas
